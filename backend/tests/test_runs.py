import threading
import time
from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient

from agent import main
from agent.tests.test_agent import StubModel, replies
from backend.main import create_app
from backend.runs import Runs
from backend.storage import Store


@pytest.mark.parametrize("amount", [420, 1250])
def test_background_activity(tmp_path, monkeypatch, amount):
    entered, release = threading.Event(), threading.Event()

    def runner(claim_id, thread_id):
        entered.set()
        assert release.wait(5)
        return main.run(claim_id, thread_id, StubModel(messages=iter(replies(amount))))

    app = create_app(tmp_path / "run.sqlite3", runner)
    with TestClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        response = client.post("/api/claims/CLM-1001/runs")
        assert response.status_code == 202
        thread = response.json()["thread_id"]
        assert entered.wait(5)
        assert client.get("/api/claims").json()[0]["status"] == "in_progress"
        assert client.post("/api/claims/CLM-1001/runs").status_code == 409
        events = client.get("/api/claims/CLM-1001/runs").json()[0]["events"]
        assert events[0]["kind"] == "started"
        release.set()
        wait_for_run(client, "completed")
        run = client.get("/api/claims/CLM-1001/runs").json()[0]
        assert run["events"][-1]["kind"] == "completed"
        assert "read_policy_wording" in {e["tool_name"] for e in run["events"]}
        assert client.post("/api/claims/CLM-1001/runs").status_code == 409
        assert (
            client.post(
                f"/api/runs/{thread}/events",
                json={"kind": "test", "tool_name": "", "text": "Too late"},
            ).status_code
            == 409
        )
    with TestClient(app) as client:
        assert (
            client.get("/api/claims/CLM-1001/runs").json()[0]["status"] == "completed"
        )


def wait_for_run(client, status):
    for _ in range(100):
        runs = client.get("/api/claims/CLM-1001/runs").json()
        if runs[-1]["status"] == status:
            return
        time.sleep(0.01)
    pytest.fail(f"Run never reached {status}")


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("Provider failed"),
        TimeoutError("Timed out"),
        ValueError("secret provider response"),
    ],
)
def test_failure_retry(tmp_path, error):
    def runner(claim_id, thread_id):
        raise error

    app = create_app(tmp_path / "failed.sqlite3", runner)
    with TestClient(app) as client:
        for expected in (1, 2):
            assert client.post("/api/claims/CLM-1001/runs").status_code == 202
            wait_for_run(client, "failed")
            runs = client.get("/api/claims/CLM-1001/runs").json()
            assert len(runs) == expected
            assert "secret" not in runs[-1]["error"]
            assert client.get("/api/claims").json()[0]["status"] == (
                "waiting" if expected == 1 else "needs_handler"
            )
        assert client.post("/api/claims/missing/runs").status_code == 404
        assert (
            client.post(
                "/api/runs/missing/events",
                json={"kind": "test", "tool_name": "", "text": "Unknown"},
            ).status_code
            == 404
        )


def test_restart_interruption(tmp_path):
    path = tmp_path / "interrupted.sqlite3"
    store = Store(path)
    store.initialize()
    runs = Runs(store, lambda c, t: "")
    previous = runs.start("CLM-1001")
    with TestClient(create_app(path)) as client:
        saved = client.get("/api/claims/CLM-1001/runs").json()[0]
        assert saved["status"] == "interrupted"
        assert saved["events"][-1]["kind"] == "interrupted"
        assert client.get("/api/claims").json()[0]["status"] == "waiting"
        assert previous.thread_id != runs.start("CLM-1001").thread_id


def test_decision_survives_failure(tmp_path, monkeypatch):
    def runner(claim_id, thread_id):
        return main.run(
            claim_id,
            thread_id,
            StubModel(messages=iter(replies(attempt_payment=False))),
        )

    app = create_app(tmp_path / "unpaid.sqlite3", runner)
    with TestClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        client.post("/api/claims/CLM-1001/runs")
        wait_for_run(client, "failed")
        detail = client.get("/api/claims/CLM-1001").json()
        assert detail["decision"]["outcome"] == "approve"
        assert detail["payment"] is None
        assert client.post("/api/claims/CLM-1001/runs").status_code == 409


def test_agent_deadline_and_inactive_logging(tmp_path, monkeypatch):
    app = create_app(tmp_path / "deadline.sqlite3")
    with TestClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        monkeypatch.setattr(main.time, "monotonic", lambda: 0)
        tools = main.tools_for("CLM-1001")
        monkeypatch.setattr(main.time, "monotonic", lambda: 121)
        with pytest.raises(TimeoutError):
            tools[0].invoke({})
        monkeypatch.undo()
        run = app.state.runs.start("CLM-1001")
        app.state.runs.finish(run.thread_id, "failed", "Stopped")
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        with pytest.raises(RuntimeError, match="Activity"):
            main.emit(run.thread_id, "tool_started", "read_claim", "Read")
