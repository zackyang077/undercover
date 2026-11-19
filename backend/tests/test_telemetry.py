import json
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from backend.db import model_usage, work_segments
from backend.main import create_app
from backend.telemetry import Telemetry


def test_usage_replay_and_missing_cost(tmp_path):
    app = create_app(tmp_path / "claims.sqlite3")
    with TestClient(app) as client:
        path = "/api/runs/thread-1/usage"
        report = {"prompt_tokens": 13, "completion_tokens": 4, "cost": 0.0001234}
        body = {
            "claim_id": "CLM-1001",
            "run_id": "run-1",
            "call_id": "call-1",
            "usage": report,
        }
        for payload in (
            body,
            body,
            {**body, "call_id": "call-2", "usage": {"cost": 0}},
            {**body, "call_id": "call-3", "run_id": None, "usage": None},
        ):
            assert client.post(path, json=payload).json() == {"status": "ok"}

        store = app.state.store
        usage, _ = Telemetry(store).for_run("run-1")
        values = {row["call_id"]: json.loads(row["usage"]) for row in usage}
        assert values == {"call-1": report, "call-2": {"cost": 0}}
        assert sum(Decimal(str(v["cost"])) for v in values.values()) == Decimal(
            "0.0001234"
        )
        with store.connection() as connection:
            standalone = connection.execute(
                select(model_usage.c.usage).where(model_usage.c.run_id.is_(None))
            ).all()
        assert standalone == [(None,)]


def test_progress_survives_interruption_and_late_replays(tmp_path):
    app = create_app(tmp_path / "claims.sqlite3")
    with TestClient(app) as client:
        path = "/api/runs/thread-1/work-segments"
        opened = {
            "segment_id": "seg-1",
            "claim_id": "CLM-1001",
            "run_id": "old-dispatch",
            "started_at": "2026-11-18T10:01:00+00:00",
            "finished_at": None,
            "elapsed_ms": 160,
        }
        closed = {
            **opened,
            "segment_id": "seg-2",
            "run_id": "new-dispatch",
            "started_at": "2026-11-19T10:01:00+08:00",
            "finished_at": "2026-11-19T10:01:00.240+08:00",
            "elapsed_ms": 240,
        }
        for payload in (
            opened,
            {**opened, "elapsed_ms": 200},
            opened,
            {**closed, "finished_at": None, "elapsed_ms": 0},
            closed,
            {**closed, "finished_at": None, "elapsed_ms": 300},
        ):
            assert client.post(path, json=payload).status_code == 200

        telemetry = Telemetry(app.state.store)
        _, (old,) = telemetry.for_run("old-dispatch")
        _, (new,) = telemetry.for_run("new-dispatch")
        assert old["elapsed_ms"] == 200
        assert old["finished_at"] is None
        assert new["elapsed_ms"] == 240
        assert new["finished_at"] == "2026-11-19T02:01:00.240000+00:00"


def test_telemetry_installs_without_reseeding_durable_database(tmp_path):
    path = tmp_path / "claims.sqlite3"
    decision = {
        "outcome": "decline",
        "payout_amount": 0,
        "explanation": "Excluded by the policy terms.",
        "calculation": "No payable benefit.",
        "policy_references": ["3"],
        "manual_references": ["1"],
    }
    endpoint = "/api/claims/CLM-1001"
    app = create_app(path)
    with TestClient(app) as client:
        response = client.post(f"{endpoint}/decision", json=decision)
        assert response.status_code == 200, response.text
        original = response.json()
        engine = app.state.store.engine
        model_usage.drop(engine)
        work_segments.drop(engine)

    reopened = create_app(path)
    with TestClient(reopened) as client:
        assert client.get(endpoint).json() == original
        schema = inspect(reopened.state.store.engine)
        assert schema.has_table("model_usage")
        assert schema.has_table("work_segments")
