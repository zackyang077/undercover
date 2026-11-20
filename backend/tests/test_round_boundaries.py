"""Exercise round decisions across paid-run, identity and transaction boundaries."""

import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend import configuration as config
from backend import improvement_routes, round_scores
from backend.db import model_usage, swarm_runs
from backend.improvement import Rounds
from backend.main import create_app
from backend.round_evaluations import Evaluations
from backend.round_scores import qualified, unit_cost
from backend.storage import Store
from backend.tests.handler_session import SignedClient, bearer
from backend.tests.test_rounds import candidate, fill, scores

LUNA = "openai/gpt-6-luna"
GEMINI = "google/gemini-3.5-flash-lite"
DANA = "HND-01"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_NAME", raising=False)
    db = Store(tmp_path / "boundaries.db")
    db.initialize()
    return db


def paid_history(store, run_id, costs, *, model=LUNA):
    with store.connection(write=True) as conn:
        conn.execute(
            swarm_runs.insert().values(
                run_id=run_id,
                kind="dispatch",
                status="completed",
                concurrency=1,
                model=model,
                created_at="2026-01-01",
                error="",
            )
        )
        for i, (claim, cost) in enumerate(costs):
            conn.execute(
                model_usage.insert().values(
                    run_id=run_id,
                    thread_id=claim,
                    claim_id=claim,
                    call_id=str(i),
                    usage=json.dumps({"cost": cost}),
                )
            )


def register(client, job, dataset):
    return client.post(
        "/api/swarm/runs/versions/" + str(job["version"]),
        json={
            "kind": "evaluation",
            "run_id": job["run_id"],
            "size": "full",
            "dataset": dataset,
            "model": LUNA,
            "reasoning_effort": "low",
            "concurrency": 25,
        },
    )


def close(client, run_id, status):
    client.post(
        f"/api/swarm/runs/{run_id}/close",
        json={
            "status": status,
            "error": "",
            "dispositions": {},
        },
    ).raise_for_status()


def test_mislabelled_trial_never_exposes_held_out_examples(tmp_path):
    app = create_app(tmp_path / "split.db")
    with SignedClient(app) as client:
        ev = Evaluations(app.state.store)
        rid = ev.rounds.create(DANA)["id"]
        planned = ev.next(rid)
        assert planned["dataset"] == "evaluation"
        registered = register(client, planned, "held_out")
        assert registered.status_code == 409
        assert not qualified(ev.store, 1, "evaluation")


def test_highest_whole_claim_cost_and_known_target_override(store):
    paid_history(store, "luna", [("a", 0.0003), ("a", 0.0027), ("b", 0.002)])
    catalog = config.allowed_models(store)
    assert unit_cost(store, LUNA, LUNA, catalog) == pytest.approx(0.003)
    assert unit_cost(store, GEMINI, LUNA, catalog) == pytest.approx(0.015)
    paid_history(store, "gemini", [("c", 0.001)], model=GEMINI)
    assert unit_cost(store, GEMINI, LUNA, catalog) == pytest.approx(0.001)


def test_can_schedule_exactly_the_remaining_dollar_amount(store):
    ev = Evaluations(store)
    rid = ev.rounds.create(DANA)["id"]
    paid_history(store, "baseline", [("a", 0.014)])
    ev.charge(rid, "first-call", 0.15)
    job = ev.next(rid)  # 25 claims at $0.014 = the remaining $0.35 exactly.
    assert job["status"] == "scheduled"
    assert not job["cold_start"]


def test_stopped_candidate_is_recorded_and_cannot_be_promoted(tmp_path):
    app = create_app(tmp_path / "stopped.db")
    with SignedClient(app) as client:
        store = app.state.store
        ev = Evaluations(store)
        rid = ev.rounds.create(DANA)["id"]
        fill(store, rid, 1, scores())
        ev.next(rid)
        candidate(ev.rounds, rid)
        trial = ev.next(rid)
        register(client, trial, trial["dataset"]).raise_for_status()
        close(client, trial["run_id"], "stopped")
        assert ev.next(rid)["verdict"] == "rolled_back"
        assert config.current(store) == 1
        saved = store.get("round_evaluations", trial["run_id"])
        assert saved["status"] == "stopped"


def test_concurrent_stop_and_promotion_agree_with_the_persisted_pointer(store):
    ev = Evaluations(store)
    rid = ev.rounds.create(DANA)["id"]
    fill(store, rid, 1, scores())
    ev.next(rid)
    version = candidate(ev.rounds, rid)["version"]
    fill(store, rid, version, scores(0.90))
    ready = Barrier(2)

    def stop():
        ready.wait(timeout=5)
        ev.rounds.stop(rid, "Changed priorities", DANA)

    def verdict():
        ready.wait(timeout=5)
        try:
            ev.next(rid)
        except HTTPException as exc:
            return exc.status_code
        return 200

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(stop), pool.submit(verdict)]
        futures[0].result(timeout=10)
        assert futures[1].result(timeout=10) in (200, 409)
    saved = ev.rounds.get(rid)
    assert saved["status"] == "stopped"
    assert config.current(store) == (version if saved["verdict"] == "kept" else 1)


def test_busy_service_refuses_review_before_any_audit_write(tmp_path, monkeypatch):

    app = create_app(tmp_path / "busy.db")
    with SignedClient(app) as client:
        store = app.state.store
        ev = Evaluations(store)
        rid = ev.rounds.create(DANA)["id"]
        fill(store, rid, 1, scores())
        ev.next(rid)
        row = ev.rounds.proposal(
            rid,
            "configuration",
            {
                "instructions": "Check coverage",
                "model": LUNA,
                "reasoning_effort": "low",
                "reason": "Clarify evidence requirements.",
            },
        )

        def busy(path, body):
            assert body["handler_id"] == DANA
            raise HTTPException(409, "A dispatch is running")

        monkeypatch.setattr(improvement_routes, "forward", busy)
        path = f"/api/improvement/rounds/{rid}/review"
        review = {
            "proposal_id": row["proposal"]["id"],
            "choice": "approve",
            "reason": "",
        }
        assert client.post(path, json=review).status_code == 409
        assert (
            client.post(
                path, json=review, headers=bearer("outsider@example.org")
            ).status_code
            == 403
        )
        assert Rounds(store).get(rid) == row
        assert config.current(store) == 1


def test_first_cold_claim_reprices_remainder_before_more_work(tmp_path):
    app = create_app(tmp_path / "probe.db")
    with SignedClient(app) as client:
        ev = Evaluations(app.state.store)
        rid = ev.rounds.create(DANA)["id"]
        trial = ev.next(rid)
        run = trial["run_id"]
        assert trial["cold_start"] is True
        register(client, trial, "evaluation").raise_for_status()
        assert ev.can_work(rid, run)["allowed"]
        copy = client.get(f"/api/swarm/runs/{run}/work").json()[0]
        member = f"/api/swarm/runs/{run}/claims/{copy}"
        thread = client.post(f"{member}/activate").json()["thread_id"]
        client.post(
            f"/api/runs/{thread}/usage",
            json={
                "call_id": "first",
                "claim_id": copy,
                "run_id": run,
                "usage": {"cost": 0.03},
            },
        ).raise_for_status()
        client.post(
            f"/api/claims/{copy}/decision",
            json={
                "outcome": "refer",
                "payout_amount": 0,
                "explanation": "Review receipt.",
                "calculation": "No payment",
                "policy_references": ["3"],
                "manual_references": ["4"],
            },
        ).raise_for_status()
        client.post(f"{member}/{thread}/finish", json={"error": ""}).raise_for_status()
        assert ev.remaining(ev.rounds.get(rid)) == Decimal("0.47")
        assert not ev.can_work(rid, run)["allowed"]  # 24 more claims cost $0.72.
        close(client, run, "stopped")
        assert ev.next(rid)["status"] == "stopped"
        assert config.current(ev.store) == 1


def test_examples_include_all_misses_and_five_weakest_correct_explanations(
    store, monkeypatch
):

    class Item:
        def __init__(self, number):
            self.value = {
                "source_id": number,
                "accurate": number >= 2,
                "grade": number / 10,
                "handler_reason": f"Evaluation example {number}",
                "same_grounds": 0.2,
                "same_section": 0.3,
            }

        def model_dump(self, *, mode):
            return self.value

    fill(store, "baseline", 1, scores())
    report = SimpleNamespace(
        dataset="evaluation", claims=[Item(i) for i in reversed(range(10))]
    )
    monkeypatch.setattr(round_scores, "evaluation_report", lambda *args, **kw: report)
    examples = round_scores.example_claims(store, 1)
    assert {row["source_id"] for row in examples["wrong"]} == {0, 1}
    assert [row["source_id"] for row in examples["explained_least_well"]] == [
        2,
        3,
        4,
        5,
        6,
    ]
    report.dataset = "held_out"
    assert round_scores.example_claims(store, 1) == {
        "wrong": [],
        "explained_least_well": [],
    }
