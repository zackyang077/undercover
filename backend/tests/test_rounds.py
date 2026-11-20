import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import configuration as config
from backend.improvement import Rounds
from backend.main import create_app
from backend.round_evaluations import Evaluations
from backend.round_scores import averages, compare, qualified, unit_cost
from backend.storage import Store
from backend.tests.handler_session import MARCUS, PRIYA, SignedClient, bearer


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "rounds.sqlite3")
    db.initialize()
    return db


def scores(accuracy=0.72, grade: float | None = 1.50, cost=0.002, seconds=10.0):
    return {
        "accuracy": accuracy,
        "grade": grade,
        "cost_per_claim": cost,
        "seconds_per_claim": seconds,
    }


def observed(store, owner, dataset, number, result, **extra):
    round_id, version = owner
    rid = f"r-{round_id}-{version}-{dataset}-{number}"
    row = {
        "round_id": round_id,
        "run_id": rid,
        "version": version,
        "dataset": dataset,
        "size": "full",
        "status": "completed",
        "created_at": str(number),
        "scores": result,
        "judge_cost": 0,
        **extra,
    }
    with store.connection(write=True) as conn:
        store.insert(conn, "round_evaluations", rid, row)


def fill(store, round_id, version, result):
    observed(store, (round_id, version), "evaluation", 1, result)
    for i in (1, 2):
        observed(store, (round_id, version), "held_out", i, result)


def candidate(rounds, rid):
    args = {
        "instructions": "Generic change",
        "model": "openai/gpt-6-luna",
        "reasoning_effort": "low",
        "reason": "Improve weak explanations",
    }
    row = rounds.proposal(rid, "configuration", args)
    return rounds.review(
        rid,
        {
            "proposal_id": row["proposal"]["id"],
            "handler_id": "HND-01",
            "reason": "",
            "choice": "approve",
        },
    )


@pytest.mark.parametrize(
    ("after", "keep"),
    [
        (scores(0.84, 1.5, 0.004, 12.5), True),
        (scores(0.84, 1.5, 0.004, 12.6), False),
        (scores(0.82), False),
        (scores(0.59, 2.3), False),
        (scores(0.95, None), False),
    ],
)
def test_held_out_verdict_and_atomic_current(store, after, keep):
    rounds = Rounds(store)
    ev = Evaluations(store)
    rid = rounds.create("HND-01")["id"]
    fill(store, rid, 1, scores())
    assert ev.next(rid)["status"] == "working"
    row = candidate(rounds, rid)
    assert row["version"] == 2
    assert config.current(store) == 1
    fill(store, rid, 2, after)
    outcome = ev.next(rid)
    assert (outcome["verdict"] == "kept") is keep
    assert config.current(store) == (2 if keep else 1)
    rounds.stop(rid, "signed out", "HND-01")
    assert config.current(store) == (2 if keep else 1)
    with pytest.raises(HTTPException):
        ev.next(rid)


def test_only_finished_full_round_trials_count_and_baseline_fills_missing(store):
    ev = Evaluations(store)
    rid = ev.rounds.create("HND-01")["id"]
    observed(store, (rid, 1), "evaluation", 1, scores())
    observed(store, (rid, 1), "held_out", 1, scores(0.8))
    observed(store, (rid, 1), "held_out", 2, scores(0.1), size="tiny")
    observed(store, ("earlier-round", 1), "held_out", 3, scores(0.1), status="stopped")
    result = averages(qualified(store, 1, "held_out"))
    assert result
    assert result["accuracy"] == 0.8
    job = ev.next(rid)
    assert job["dataset"] == "held_out"
    assert job["version"] == 1
    assert job["cold_start"]
    assert ev.next(rid)["run_id"] == job["run_id"]
    assert ev.rounds.get(rid)["progress"] == 3
    assert len(store.queue()) == 25


def test_round_snapshot_refusal_no_charge_replay_and_missing_grade(store):
    ev = Evaluations(store)
    rid = ev.rounds.create("HND-01")["id"]
    settings = config.business_settings(store)
    config.update_settings(
        store, config.BusinessSettings(**{**settings, "budget": 2.0}), "HND-01"
    )
    assert ev.rounds.get(rid)["settings"]["budget"] == 0.5
    with pytest.raises(HTTPException):
        ev.rounds.create("HND-01")
    fill(store, rid, 1, scores())
    ev.next(rid)
    candidate(ev.rounds, rid)
    ev.charge(rid, "model-call", 0.5)
    ev.charge(rid, "model-call", 0.5)
    assert ev.costs(ev.rounds.get(rid))["total"] == 0.5
    assert ev.next(rid)["verdict"] == "rolled_back"
    ev.rounds.finish(rid)
    assert config.current(store) == 1
    assert compare(scores(), scores(grade=None), settings)[0] is False


def test_proposals_recovery_and_rejection_are_audited(store):
    ev = Evaluations(store)
    rounds = ev.rounds
    rid = rounds.create("HND-01")["id"]
    fill(store, rid, 1, scores())
    ev.next(rid)
    args = {
        "model": "out-of-catalog",
        "instructions": "read",
        "reason": "test",
        "reasoning_effort": "high",
    }
    with pytest.raises(HTTPException):
        rounds.proposal(rid, "configuration", args)
    assert rounds.get(rid)["proposal"] is None
    args.update(model="openai/gpt-6-luna", reasoning_effort="low")
    row = rounds.proposal(rid, "configuration", args)
    rounds.recover()
    assert rounds.get(rid)["status"] == "configuration_review"
    assert len(store.all("configuration")) == 1
    rounds.review(
        rid,
        {
            "proposal_id": row["proposal"]["id"],
            "choice": "reject",
            "reason": "Too broad",
            "handler_id": "HND-01",
        },
    )
    assert rounds.get(rid)["reviews"][0]["reason"] == "Too broad"
    rounds.recover()
    assert rounds.get(rid)["status"] == "stopped"
    assert config.current(store) == 1


@pytest.mark.parametrize("operation", ["start", "stop", "review", "setting"])
def test_only_senior_handlers_can_change_improvement_state(tmp_path, operation):
    app = create_app(tmp_path / "actions.sqlite3")
    paths = {
        "start": "/api/improvement/rounds",
        "stop": "/api/improvement/rounds/id/stop",
        "review": "/api/improvement/rounds/id/review",
        "setting": "/api/settings",
    }
    with TestClient(app) as client:
        body = (
            {"proposal_id": "id", "choice": "approve", "reason": ""}
            if operation == "review"
            else {}
        )
        if operation == "setting":
            body = config.business_settings(app.state.store)
        for headers, status in (({}, 401), (bearer(MARCUS), 403), (bearer(PRIYA), 403)):
            method = client.put if operation == "setting" else client.post
            assert (
                method(paths[operation], json=body, headers=headers).status_code
                == status
            )
        assert not app.state.store.all("rounds")
        assert not app.state.store.all("setting_changes")


def test_reports_limit_references_and_serve_live_aggregate_history(tmp_path):
    app = create_app(tmp_path / "reports.sqlite3")
    with SignedClient(app) as client:
        rid = client.post("/api/improver/rounds", json={"handler_id": "HND-01"}).json()[
            "id"
        ]
        path = f"/api/improver/rounds/{rid}"
        for _ in range(3):
            job = client.post(path + "/next-evaluation").json()
            request = {
                "run_id": job["run_id"],
                "kind": "evaluation",
                "dataset": job["dataset"],
                "size": "full",
                "concurrency": 25,
                "model": "openai/gpt-6-luna",
                "reasoning_effort": "low",
            }
            client.post("/api/swarm/runs/versions/1", json=request).raise_for_status()
            client.post(
                "/api/swarm/runs/" + job["run_id"] + "/close",
                json={"status": "completed", "error": "", "dispositions": {}},
            ).raise_for_status()
        assert client.post(path + "/next-evaluation").json()["status"] == "working"
        report = client.get(path + "/results").json()
        assert set(report["scores"]["held_out"]) == set(scores())
        assert report["evaluation_set"]["wrong"]
        assert len(report["evaluation_set"]["explained_least_well"]) <= 5
        ids = [r["source_id"] for r in report["evaluation_set"]["wrong"]]
        held_runs = qualified(app.state.store, 1, "held_out")
        held = client.get("/api/evaluations/" + held_runs[-1]["run_id"]).json()[
            "claims"
        ]
        assert not set(ids).intersection(r["source_id"] for r in held)
        for row in held:
            assert row["claim_id"] not in json.dumps(report)
        assert (
            client.get("/api/configuration").json()["versions"][0]["held_out"]["grade"]
            == 0
        )
        assert client.post(path + "/finish").json()["version"] is None
        assert len(client.get("/api/improvement").json()["rounds"]) == 1


def test_estimate_scales_prices_and_budget_stops_new_claims(tmp_path):
    app = create_app(tmp_path / "cost.db")
    with SignedClient(app) as client:
        store = app.state.store
        ev = Evaluations(store)
        rid = ev.rounds.create("HND-01")["id"]
        job = ev.next(rid)
        run_id = job["run_id"]
        req = {
            "run_id": run_id,
            "kind": "evaluation",
            "dataset": "evaluation",
            "size": "full",
            "model": "openai/gpt-6-luna",
            "reasoning_effort": "low",
            "concurrency": 25,
        }
        client.post("/api/swarm/runs/versions/1", json=req).raise_for_status()
        claim = client.get(f"/api/swarm/runs/{run_id}/work").json()[0]
        assert ev.can_work(rid, run_id)["allowed"]
        thread = client.post(
            f"/api/swarm/runs/{run_id}/claims/{claim}/activate"
        ).json()["thread_id"]
        client.post(
            f"/api/runs/{thread}/usage",
            json={
                "claim_id": claim,
                "run_id": run_id,
                "call_id": "one",
                "usage": {"cost": 0.01},
            },
        ).raise_for_status()
        cost = unit_cost(
            store,
            "google/gemini-3.5-flash-lite",
            req["model"],
            config.allowed_models(store),
        )
        assert cost == 0.05
        ev.charge(rid, "paid", 0.49)
        assert not ev.can_work(rid, run_id)["allowed"]
        client.post(
            f"/api/swarm/runs/{run_id}/close",
            json={"status": "stopped", "error": "Spending limit", "dispositions": {}},
        ).raise_for_status()
        assert ev.next(rid)["status"] == "stopped"
        assert client.get(f"/api/evaluations/{run_id}").json()["status"] == "stopped"
        assert len(store.queue()) == 25
