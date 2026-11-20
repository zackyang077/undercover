import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from time import monotonic, sleep

import httpx
import pytest
from openrouter import OpenRouter
from sqlalchemy import select

from backend import judge as judge_module
from backend.eval_sources import handler_references
from backend.judge import JEV, Judge
from backend.main import create_app
from backend.tests.handler_session import SignedClient
from backend.tests.judge_server import decisions as fixture_response
from backend.tests.test_finance import decision

CLOSE = {"status": "completed", "error": "", "dispositions": {}}


def create_eval(client, run_id="trial", size="tiny", dataset="evaluation"):
    client.post(
        "/api/swarm/runs",
        json={
            "kind": "evaluation",
            "run_id": run_id,
            "model": "stub",
            "concurrency": 25,
            "reasoning_effort": None,
            "size": size,
            "dataset": dataset,
        },
    ).raise_for_status()
    return client.get(f"/api/swarm/runs/{run_id}/work").json()


def test_grading_closes_once_in_parallel_outside_the_database(tmp_path, monkeypatch):
    team = Barrier(24)
    requests = []
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.setenv("OPENROUTER_JUDGE_URL", "http://decisions.test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")

    def transport(request):
        assert str(request.url) == "http://decisions.test/api/alpha/decisions"
        assert request.extensions["timeout"]["read"] == 10
        body = json.loads(request.content)
        assert set(body["questions"]) == {"grade", "same_grounds", "same_section"}
        assert body["model"] == JEV
        assert body["state"]["handler"]["reference"]
        assert body["state"]["agent"]["section"] == ["3", "10"]
        requests.append(body)
        team.wait(timeout=8)
        if body["state"]["agent"]["rationale"] == "timeout":
            with app.state.store.connection(write=True) as conn:
                app.state.store.insert(conn, "test", "grading-in-progress", {})
            sleep(request.extensions["timeout"]["read"])
            raise httpx.ReadTimeout("Timeout")
        return httpx.Response(200, json=fixture_response(body))

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        monkeypatch.setattr(
            judge_module,
            "OpenRouter",
            lambda api_key: OpenRouter(api_key=api_key, client=http),
        )
        app = create_app(tmp_path / "judge.db")
        with SignedClient(app) as client:
            copies = create_eval(client, size="full")
            for n, copy in enumerate(copies[:-1]):
                payload = {
                    **decision(),
                    "explanation": "timeout" if n == 0 else "Reviewed",
                }
                client.post(
                    f"/api/claims/{copy}/decision", json=payload
                ).raise_for_status()
            path = "/api/swarm/runs/trial/close"
            start = monotonic()
            with ThreadPoolExecutor(2) as pool:
                responses = list(
                    pool.map(lambda _: client.post(path, json=CLOSE), range(2))
                )
            assert monotonic() - start < 20
            assert all(r.is_success for r in responses)
            for _ in range(2):
                result = client.get("/api/evaluations/trial").json()
                assert result["score"]["grade"] == pytest.approx(1.42 * 23 / 24)
                assert Decimal(result["score"]["judge_cost"]) == Decimal("0.00023")
                assert Decimal(result["score"]["model_cost"]) == 0
                items = {r["claim_id"]: r for r in result["claims"]}
                assert items[copies[0]]["grade"] is None
                assert items[copies[-1]]["letter"] == "F"
                assert items[copies[-1]]["grade"] == 0
                graded = items[copies[1]]
                assert graded["grade"] == 1.42
                assert graded["letter"] == "F"
                assert graded["same_grounds"] == 0.9
                assert graded["same_section"] == 0.3
            assert len(requests) == 24


def test_free_evaluation_skips_every_judgment(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "override")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")

    def no_calls(*args, **kwargs):
        pytest.fail("Free evaluation tried the judge")

    monkeypatch.setattr(judge_module, "OpenRouter", no_calls)
    with SignedClient(create_app(tmp_path / "free.db")) as client:
        first, *_ = create_eval(client, dataset="held_out")
        client.post(f"/api/claims/{first}/decision", json=decision()).raise_for_status()
        client.post("/api/swarm/runs/trial/close", json=CLOSE).raise_for_status()
        result = client.get("/api/evaluations/trial").json()
        assert result["score"]["grade"] is None
        assert Decimal(result["score"]["judge_cost"]) == 0
        assert all(r["letter"] is None for r in result["claims"])
        assert all(
            r["handler_reason"] is None and r["handler_reference"] is None
            for r in result["claims"]
        )


def test_judge_without_key_skips_decided_claim(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    grade = Judge().batch([{"agent": {"decision": "approve"}}])[0]
    assert grade.grade is None


def test_legacy_answer_keys_gain_reasons_without_reseeding(tmp_path):
    app = create_app(tmp_path / "legacy.db")
    with SignedClient(app) as client:
        copies = create_eval(client)
    store = app.state.store
    with store.connection(write=True) as conn:
        conn.exec_driver_sql("ALTER TABLE handler_references DROP COLUMN reason")
        conn.exec_driver_sql("ALTER TABLE handler_references DROP COLUMN reference")
    store.initialize()
    with store.connection() as conn:
        rows = conn.execute(select(handler_references)).all()
        assert len(rows) == 50
        assert all(r.reason and r.reference for r in rows)
    assert store.detail(copies[0]).evaluation_copy


def test_sdk_does_not_retry_an_unavailable_judge(monkeypatch):
    requests = []

    def unavailable(request):
        requests.append(request.url)
        return httpx.Response(503, json={"error": {"code": 503, "message": "Offline"}})

    with httpx.Client(transport=httpx.MockTransport(unavailable)) as http:
        monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
        monkeypatch.delenv("MODEL_NAME", raising=False)
        monkeypatch.setattr(
            judge_module,
            "OpenRouter",
            lambda api_key: OpenRouter(api_key=api_key, client=http),
        )
        grade = Judge().batch([{"agent": {"decision": "approve"}}])[0]
    assert grade.grade is None
    assert len(requests) == 1
