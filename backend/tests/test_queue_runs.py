import asyncio
import threading
import time
from contextlib import nullcontext

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agent import main
from agent.queue import run_queue
from agent.tests.test_agent import StubModel, call, replies
from backend.finance import Finance
from backend.main import create_app
from backend.queue_runs import CappedClaims, QueueRuns
from backend.runs import Runs
from backend.schemas import DecisionInput
from backend.storage import Store
from backend.tests.queue_stub import scripted_queue
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
    for _ in range(500):
        saved = client.get(CURRENT).json()
        if saved["status"] == status:
            return saved
        if saved["status"] == "failed" and status != "failed":
            pytest.fail(saved["error"])
        time.sleep(0.01)
    pytest.fail(f"Queue never reached {status}")


def test_agent_order_cap_retry_and_handler_handoff(tmp_path, monkeypatch):
    visited = []
    counts = {}

    def child(claim_id, thread_id):
        visited.append((claim_id, thread_id))
        counts[claim_id] = counts.get(claim_id, 0) + 1
        if claim_id == "CLM-1005" or (claim_id == "CLM-1004" and counts[claim_id] == 1):
            raise RuntimeError("Scripted timeout")
        amount = 1250 if claim_id == "CLM-1003" else 420
        return main.run(claim_id, thread_id, StubModel(messages=iter(replies(amount))))

    app = create_app(
        tmp_path / "agent-queue.sqlite3", child, scripted_queue(child, reverse=True)
    )
    with TestClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        restrict_queue(app.state.store, {f"CLM-{1000 + i}" for i in range(1, 7)})
        result = client.post(
            "/api/queue/runs", json={"mode": "capped", "max_claims": 5}
        )
        assert result.status_code == 202
        saved = wait_for_queue(client, "completed")
        assert visited[0][0] == "CLM-1006"
        assert len(saved["admitted"]) == 5
        assert counts["CLM-1005"] == counts["CLM-1004"] == 2
        assert len({thread for _, thread in visited}) == len(visited)
        assert client.get("/api/claims/CLM-1001").json()["status"] == "waiting"
        failed = client.get("/api/claims/CLM-1005").json()
        assert failed["status"] == "needs_handler"
        assert failed["decision"] is None
        assert client.get("/api/claims/CLM-1003").json()["status"] == "waiting_sign_off"
        assert (
            client.post(
                "/api/claims/CLM-1003/payment", json={"amount": 1250}
            ).status_code
            == 403
        )
        assert saved["summary"] == "4 completed; 1 technical failures."
        assert {t["status"] for t in saved["todos"]} == {"done", "needs_handler"}
        assert client.post("/api/claims/CLM-1005/runs").status_code == 409
        assert (
            client.post(f"/api/queue/runs/{saved['queue_run_id']}/stop").status_code
            == 409
        )
        thread = next(
            i["thread_id"] for i in saved["items"] if i["claim_id"] == "CLM-1005"
        )
        client.post(
            f"/api/queue/runs/{saved['queue_run_id']}/attempts/{thread}/finish",
            json={"error": "repeat"},
        )
        assert app.state.store.failures("CLM-1005") == 2
    with TestClient(app) as client:
        assert client.get(CURRENT).json()["status"] == "completed"
        assert client.get("/api/claims/CLM-1005").json()["status"] == "needs_handler"


def test_stop_and_shutdown_wait_for_the_active_helper(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    visited = []

    def child(claim_id, thread_id):
        visited.append(claim_id)
        entered.set()
        assert release.wait(5)
        return main.run(claim_id, thread_id, StubModel(messages=iter(replies())))

    app = create_app(tmp_path / "stop.sqlite3", child, scripted_queue(child))
    with TestClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        created = client.post("/api/queue/runs", json={"mode": "all"}).json()
        assert entered.wait(5)
        assert client.post("/api/queue/runs", json={"mode": "all"}).status_code == 409
        assert client.post(f"/api/queue/runs/{created['queue_run_id']}/stop").json()[
            "stop_requested"
        ]
        release.set()
        saved = wait_for_queue(client, "stopped")
        assert visited == ["CLM-1001"]
        assert saved["items"][0]["status"] == "completed"
        assert all(i["status"] == "pending" for i in saved["items"][1:])
        assert client.post("/api/queue/runs/missing/stop").status_code == 404
        # A different request cap must not replace the unfinished batch.
        resumed = app.state.queue.start(CappedClaims(mode="capped", max_claims=1))
        assert resumed.queue_run_id == created["queue_run_id"]
        assert resumed.thread_id == created["thread_id"]
        assert resumed.target == 25
        assert resumed.admitted == ["CLM-1001"]
        assert not resumed.stop_requested


def test_completion_guard_corrects_missed_claim_and_rejects_false_finish(
    tmp_path, monkeypatch
):
    def child(claim_id, thread_id):
        return main.run(claim_id, thread_id, StubModel(messages=iter(replies())))

    def parent(queue_id, thread):
        messages = [
            AIMessage(content="Premature finish"),
            call("delegate_claim", {"claim_id": "CLM-1002"}),
            AIMessage(content="One completed."),
        ]
        return run_queue(queue_id, thread, StubModel(messages=iter(messages)), child)

    app = create_app(tmp_path / "missed.sqlite3", child, parent)
    with TestClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        created = app.state.queue.start(CappedClaims(mode="capped", max_claims=1))
        with pytest.raises(RuntimeError, match="missed"):
            app.state.queue.finish(created.queue_run_id, "completed")
        asyncio.run(app.state.queue.execute(created))
        assert wait_for_queue(client, "completed")["admitted"] == ["CLM-1002"]
        app.state.queue.runner = lambda q, t: run_queue(
            q,
            t,
            StubModel(
                messages=iter(
                    [AIMessage(content="Done"), AIMessage(content="Still done")]
                )
            ),
            child,
        )
        client.post("/api/queue/runs", json={"mode": "capped", "max_claims": 1})
        failed = wait_for_queue(client, "failed")
        assert "missed claims" in failed["error"]
        assert not any(
            c["status"] == "in_progress" for c in client.get("/api/claims").json()
        )


def test_empty_restart_and_unconfigured_queue(tmp_path):
    store = Store(tmp_path / "recover.sqlite3")
    store.initialize()
    runs = Runs(store, lambda c, t: "")
    queue = QueueRuns(runs)
    batch = queue.start(CappedClaims(mode="capped", max_claims=1))
    queue.admit(batch.queue_run_id, "CLM-1002")
    with TestClient(create_app(store.path, lambda c, t: "")) as client:
        assert client.get(CURRENT).json()["status"] == "interrupted"
        assert client.get("/api/claims/CLM-1002").json()["status"] == "waiting"
        assert store.failures("CLM-1002") == 0
        restrict_queue(store, set())
        assert client.post("/api/queue/runs", json={"mode": "all"}).status_code == 409
        with pytest.raises(HTTPException):
            queue.admit(batch.queue_run_id, "CLM-1001")


def test_externally_resolved_candidate_and_orphan_cleanup(tmp_path, monkeypatch):
    app = create_app(tmp_path / "external.sqlite3")
    with TestClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        batch = app.state.queue.start(CappedClaims(mode="capped", max_claims=1))
        client.post("/api/claims/CLM-1001/decision", json=decision(0, "refer"))
        prefix = f"/api/queue/runs/{batch.queue_run_id}"
        assert client.post(f"{prefix}/claims/CLM-1001/attempts").status_code == 409
        check = client.get(f"{prefix}/check").json()
        assert "CLM-1001" not in check["remaining"]
        assert check["unused_slots"] == 1
        assert (
            client.post(
                f"{prefix}/attempts/unknown/finish", json={"error": ""}
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/queue/runs/unknown/attempts/unknown/finish", json={"error": ""}
            ).status_code
            == 404
        )

        def broken(queue_id, thread_id):
            app.state.queue.admit(queue_id, "CLM-1002")
            raise RuntimeError("Parent stopped before reporting its child")

        app.state.queue.runner = broken
        asyncio.run(app.state.queue.execute(batch))
        assert client.get(CURRENT).json()["status"] == "failed"
        assert client.get("/api/claims/CLM-1002").json()["status"] == "waiting"
        assert client.get("/api/claims/CLM-1002/runs").json()[-1]["status"] != "running"
