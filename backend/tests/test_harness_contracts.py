from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.queue_runs import CappedClaims, QueueRuns, Todo, Todos
from backend.runs import Runs
from backend.storage import Store


def test_cap_locks_todos_and_restart(tmp_path):
    path = tmp_path / "contracts.sqlite3"
    store = Store(path)
    store.initialize()
    runs = Runs(store, lambda c, t: "")
    queue = QueueRuns(runs)
    batch = queue.start(CappedClaims(mode="capped", max_claims=1))
    task = Todo(claim_id="CLM-1003", text="Review the claim", status="pending")
    queue.write_todos(batch.queue_run_id, Todos(items=[task]))
    admitted = queue.admit(batch.queue_run_id, "CLM-1003")
    current = queue.current()
    assert current is not None
    assert current.todos[0].status == "in_progress"
    with pytest.raises(HTTPException) as error:
        queue.admit(batch.queue_run_id, "CLM-1004")
    assert error.value.status_code == 409
    runs.finish(admitted["thread_id"], "failed", "First failure")
    with TestClient(create_app(path, lambda c, t: "")) as client:
        assert client.get("/api/claims/CLM-1003").json()["status"] == "waiting"
        assert client.get("/api/queue/runs/current").json()["status"] == "interrupted"
    next_batch = queue.start(CappedClaims(mode="capped", max_claims=1))
    assert next_batch.queue_run_id == batch.queue_run_id
    retry = queue.admit(next_batch.queue_run_id, "CLM-1003")
    runs.finish(retry["thread_id"], "failed", "Second failure")
    runs.finish(retry["thread_id"], "failed", "Duplicate report")
    assert store.failures("CLM-1003") == 2
    assert store.detail("CLM-1003").status == "needs_handler"


@pytest.mark.parametrize("value", [0, -1, 1.5, "5", None])
def test_invalid_caps(tmp_path, value):
    with TestClient(create_app(tmp_path / "invalid.sqlite3")) as client:
        assert (
            client.post(
                "/api/queue/runs", json={"mode": "capped", "max_claims": value}
            ).status_code
            == 422
        )


def test_atomic_admission_and_completion_validation(tmp_path):
    app = create_app(tmp_path / "atomic.sqlite3")
    with TestClient(app) as client:
        queue = app.state.queue
        batch = queue.start(CappedClaims(mode="capped", max_claims=1))
        prefix = f"/api/queue/runs/{batch.queue_run_id}"
        with ThreadPoolExecutor() as pool:
            results = list(
                pool.map(
                    lambda c: client.post(f"{prefix}/claims/{c}/attempts"),
                    ["CLM-1001", "CLM-1002"],
                )
            )
        assert sorted(r.status_code for r in results) == [200, 409]
        assert client.post("/api/claims/CLM-1004/runs").status_code == 409
        assert client.get(f"{prefix}/check").json()["complete"] is False
        assert client.put(f"{prefix}/todos", json={"items": []}).status_code == 409
        assert (
            client.put(
                f"{prefix}/todos",
                json={
                    "items": [
                        {
                            "claim_id": queue.current().admitted[0],
                            "text": "Fake completion",
                            "status": "done",
                        }
                    ]
                },
            ).status_code
            == 409
        )
        assert client.post(f"{prefix}/claims/outside/attempts").status_code == 409
        assert client.get("/api/queue/runs/unknown/check").status_code == 404
        queue.finish(batch.queue_run_id, "stopped")


def test_five_unique_slots_and_upgrade_preserves_records(tmp_path):
    store = Store(tmp_path / "five.sqlite3")
    store.initialize()
    runs = Runs(store, lambda c, t: "")
    queue = QueueRuns(runs)
    batch = queue.start(CappedClaims(mode="capped", max_claims=5))
    assert batch.target == 5
    for number in range(1, 6):
        started = queue.admit(batch.queue_run_id, f"CLM-{1000 + number}")
        runs.finish(started["thread_id"], "failed", "Temporary failure")
        with queue.editing(batch.queue_run_id) as current:
            current.items[number - 1].status = "failed"
    with pytest.raises(HTTPException):
        queue.admit(batch.queue_run_id, "CLM-1006")
    retry = queue.admit(batch.queue_run_id, "CLM-1001")
    current = queue.current()
    assert current is not None
    assert len(current.admitted) == 5
    runs.finish(retry["thread_id"], "failed", "Second failure")
    queue.finish(batch.queue_run_id, "stopped")
    store.initialize()
    assert len(runs.list("CLM-1001")) == 2
    assert store.detail("CLM-1001").status == "needs_handler"
    resumed = queue.start()
    assert resumed.target == 5
    assert resumed.admitted == [f"CLM-{n}" for n in range(1001, 1006)]
