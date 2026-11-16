import asyncio
import threading
import time
from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient

from agent import main
from agent.tests.test_agent import StubModel, replies
from backend.finance import Finance
from backend.main import create_app
from backend.queue_runs import QueueRuns
from backend.runs import Runs
from backend.schemas import DecisionInput
from backend.storage import Store
from backend.tests.test_finance import decision

CURRENT = "/api/queue/runs/current"


def restrict_queue(store, claim_ids):
    finance = Finance(store)
    for claim in store.all("claims"):
        if (
            claim["claim_id"] not in claim_ids
            and not store.detail(claim["claim_id"]).decision
        ):
            finance.decide(
                claim["claim_id"], DecisionInput.model_validate(decision(0, "refer"))
            )


def wait_for_queue(client, status):
    for _ in range(200):
        saved = client.get(CURRENT).json()
        if saved["status"] == status:
            return saved
        time.sleep(0.01)
    pytest.fail(f"Queue never reached {status}")


def test_sequential_queue_skips_decisions_and_continues_after_failure(
    tmp_path, monkeypatch
):
    entered, release = threading.Event(), threading.Event()
    visited = []
    active = 0

    def runner(claim_id, thread_id):
        nonlocal active
        active += 1
        assert active == 1
        visited.append(claim_id)
        if claim_id == "CLM-1001":
            entered.set()
            assert release.wait(5)
        try:
            if claim_id == "CLM-1002":
                raise RuntimeError("Scripted model failure")
            amount = 1250 if claim_id == "CLM-1003" else 420
            return main.run(
                claim_id, thread_id, StubModel(messages=iter(replies(amount)))
            )
        finally:
            active -= 1

    app = create_app(tmp_path / "queue.sqlite3", runner)
    with TestClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        assert client.get(CURRENT).json() is None
        restrict_queue(app.state.store, {"CLM-1001", "CLM-1002", "CLM-1003"})
        response = client.post("/api/queue/runs", json={"mode": "all"})
        assert response.status_code == 202
        assert [i["claim_id"] for i in response.json()["items"]] == [
            "CLM-1001",
            "CLM-1002",
            "CLM-1003",
        ]
        assert entered.wait(5)
        assert client.post("/api/queue/runs", json={"mode": "all"}).status_code == 409
        release.set()
        saved = wait_for_queue(client, "completed")
        assert [i["status"] for i in saved["items"]] == [
            "completed",
            "failed",
            "completed",
        ]
        assert visited == ["CLM-1001", "CLM-1002", "CLM-1003"]
        assert client.get("/api/claims/CLM-1001").json()["payment"]["amount"] == 420
        assert client.get("/api/claims/CLM-1003").json()["status"] == "waiting_sign_off"
        assert (
            client.post(
                "/api/claims/CLM-1003/payment", json={"amount": 1250}
            ).status_code
            == 403
        )
        assert client.get("/api/claims/CLM-1002").json()["status"] == "waiting"
        assert (
            client.post(f"/api/queue/runs/{saved['queue_run_id']}/stop").status_code
            == 409
        )
    with TestClient(app) as client:
        assert client.get(CURRENT).json()["queue_run_id"] == saved["queue_run_id"]
        assert client.get(CURRENT).json()["status"] == "completed"


def test_stop_finishes_current_and_skips_claim_decided_by_another_caller(
    tmp_path, monkeypatch
):
    entered, release = threading.Event(), threading.Event()
    visited = []

    def runner(claim_id, thread_id):
        visited.append(claim_id)
        entered.set()
        assert release.wait(5)
        return main.run(claim_id, thread_id, StubModel(messages=iter(replies())))

    app = create_app(tmp_path / "stop.sqlite3", runner)
    with TestClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        restrict_queue(app.state.store, {"CLM-1001", "CLM-1002", "CLM-1003"})
        created = client.post("/api/queue/runs", json={"mode": "all"}).json()
        assert entered.wait(5)
        stopped = client.post(f"/api/queue/runs/{created['queue_run_id']}/stop")
        assert stopped.json()["stop_requested"] is True
        release.set()
        saved = wait_for_queue(client, "stopped")
        assert [i["status"] for i in saved["items"]] == [
            "completed",
            "pending",
            "pending",
        ]
        assert visited == ["CLM-1001"]
        assert client.post("/api/queue/runs/missing/stop").status_code == 404
        app.state.queue.runs.runner = lambda c, t: ""
        created = app.state.queue.start()
        client.post("/api/claims/CLM-1002/decision", json=decision(0, "refer"))
        asyncio.run(app.state.queue.process(created.queue_run_id, 0, "CLM-1002"))
        assert client.get(CURRENT).json()["items"][0]["status"] == "skipped"
        app.state.queue.finish(created.queue_run_id, "stopped")


def test_empty_active_and_restart_queue(tmp_path):
    store = Store(tmp_path / "recover.sqlite3")
    store.initialize()
    runs = Runs(store, lambda c, t: "")
    queue = QueueRuns(runs)
    restrict_queue(store, {"CLM-1001", "CLM-1002"})
    active = runs.start("CLM-1002")
    batch = queue.start()
    assert [item.claim_id for item in batch.items] == ["CLM-1001"]
    with queue.editing(batch.queue_run_id) as saved:
        saved.items[0].status = "running"
    with TestClient(create_app(store.path)) as client:
        saved = client.get(CURRENT).json()
        assert saved["status"] == "interrupted"
        assert saved["items"][0]["status"] == "failed"
        assert (
            client.get("/api/claims/CLM-1002/runs").json()[0]["thread_id"]
            == active.thread_id
        )
        restrict_queue(store, set())
        assert client.post("/api/queue/runs", json={"mode": "all"}).status_code == 409


def test_shutdown_stops_after_current_claim(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    visited = []

    def runner(claim_id, thread_id):
        visited.append(claim_id)
        entered.set()
        assert release.wait(5)
        return "done"

    app = create_app(tmp_path / "close.sqlite3", runner)
    original_stop = app.state.queue.stop

    def stop(queue_run_id):
        result = original_stop(queue_run_id)
        release.set()
        return result

    monkeypatch.setattr(app.state.queue, "stop", stop)
    with TestClient(app) as client:
        client.post("/api/queue/runs", json={"mode": "all"})
        assert entered.wait(5)
    assert visited == ["CLM-1001"]
    assert app.state.queue.current().status == "stopped"
