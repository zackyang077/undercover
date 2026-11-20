from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import select

from agent import main, service
from agent.checkpoints import open_saver
from agent.tests.test_agent import StubModel, replies
from backend.main import create_app
from backend.swarm import swarm_runs
from backend.tests.handler_session import SignedClient

CLAIM_ID = "CLM-1001"
CLAIM = f"/api/claims/{CLAIM_ID}"


@pytest.mark.parametrize("warm_thread", [True, False])
def test_signed_checkpoint_fast_path_requires_matching_cached_thread(
    tmp_path, monkeypatch, warm_thread
):
    app = create_app(tmp_path / "signed-thread.sqlite3")
    with SignedClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        stub = StubModel(messages=iter(replies(1250)))
        monkeypatch.setattr(main, "model", lambda: stub)
        worker = service.Worker()
        runs = app.state.runs
        thread = runs.start(CLAIM_ID).thread_id
        assert worker.run_claim(CLAIM_ID, thread) == "Waiting for sign-off"
        assert runs.conclude(CLAIM_ID, thread, "").status == "paused"
        with open_saver() as saver:
            history = list(saver.list({"configurable": {"thread_id": thread}}))
            assert history[0].checkpoint["channel_values"]["messages"] == []
            assert any(
                cp.checkpoint["channel_values"].get("messages") for cp in history
            )
        signed_url = f"/api/work/{CLAIM_ID}/signed-thread"
        assert client.get(signed_url).json() is None
        worker.resume(CLAIM_ID)
        assert client.get(CLAIM).json()["payment"] is None
        signed = client.post(f"{CLAIM}/sign-off", json={})
        signed.raise_for_status()
        assert client.get(signed_url).json() == {
            "thread_id": thread,
            "sign_off": signed.json(),
        }

        calls = []
        request = service.api

        def capture(method, path, body=None):
            calls.append(path)
            return request(method, path, body)

        monkeypatch.setattr(service, "api", capture)
        if warm_thread:
            monkeypatch.setattr(
                CompiledStateGraph,
                "get_state",
                lambda *a, **kw: pytest.fail("Redundant checkpoint read"),
            )
        else:
            worker.waiting_threads[CLAIM_ID] = "stale-thread"
        worker.resume(CLAIM_ID)
        assert calls[0] == signed_url
        assert (f"/api/work/{CLAIM_ID}/activate" in calls) == (not warm_thread)
        paid = client.get(CLAIM).json()["payment"]
        assert paid["amount"] == 1250
        assert paid["sign_off"] == signed.json()
        assert client.get(signed_url).json() is None


def test_restart_after_missed_signoff_and_duplicate_notifications(
    tmp_path, monkeypatch
):
    path = tmp_path / "signed.db"
    app = create_app(path)
    with SignedClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        runs = app.state.runs
        thread = runs.start(CLAIM_ID).thread_id
        main.run(CLAIM_ID, thread, StubModel(messages=iter(replies(1250))))
        assert runs.conclude(CLAIM_ID, thread, "").status == "paused"
        result = client.post(f"{CLAIM}/sign-off", json={})
        assert result.is_success
        assert client.get(CLAIM).json()["status"] == "in_progress"

    app = create_app(path)
    with SignedClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        worker = service.Worker(
            lambda c, t: main.run(c, t, StubModel(messages=iter([])))
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(worker.resume, CLAIM_ID), pool.submit(worker.drain)]
            for job in jobs:
                job.result(timeout=10)
        record = client.get(CLAIM).json()
        assert record["payment"]["amount"] == 1250
        assert record["payment"]["sign_off"] == record["sign_off"]
        assert client.get(f"{CLAIM}/runs").json()[0]["thread_id"] == thread
        assert len(app.state.store.all("payments")) == 1


def test_failed_signed_payment_remains_recoverable_on_original_thread(
    tmp_path, monkeypatch
):
    app = create_app(tmp_path / "failed-payment.sqlite3")
    with SignedClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        monkeypatch.setattr(
            main, "model", lambda: StubModel(messages=iter(replies(1250)))
        )
        runs = app.state.runs
        thread = runs.start(CLAIM_ID).thread_id
        worker = service.Worker()
        worker.resume(CLAIM_ID)
        client.post(f"{CLAIM}/sign-off", json={}).raise_for_status()

        request = main.request

        def fail_payment(method, path, body=None):
            if path == f"{CLAIM}/payment":
                raise RuntimeError("Simulated payment failure")
            return request(method, path, body)

        with monkeypatch.context() as patch:
            patch.setattr(main, "request", fail_payment)
            worker.resume(CLAIM_ID)
        assert client.get(CLAIM).json()["payment"] is None
        (interrupted,) = runs.list(CLAIM_ID)
        assert interrupted.status == "interrupted"
        assert interrupted.error == "Simulated payment failure"

        worker.resume(CLAIM_ID)
        paid = client.get(CLAIM).json()["payment"]
        assert paid["amount"] == 1250
        (completed,) = runs.list(CLAIM_ID)
        assert (completed.thread_id, completed.status) == (thread, "completed")


def test_startup_stops_dispatch_without_resuming_its_investigation(
    tmp_path, monkeypatch
):
    path = tmp_path / "startup.sqlite3"
    run_id = "interrupted-run"
    prefix = f"/api/swarm/runs/{run_id}"
    with SignedClient(create_app(path)) as backend:
        backend.post(
            "/api/swarm/runs",
            json={
                "run_id": run_id,
                "kind": "dispatch",
                "model": main.DEFAULT_MODEL,
                "reasoning_effort": "low",
                "dataset": None,
                "size": None,
                "concurrency": 3,
            },
        ).raise_for_status()
        response = backend.post(f"{prefix}/claims/{CLAIM_ID}/activate")
        response.raise_for_status()
        thread = response.json()["thread_id"]

    app = create_app(path)
    with SignedClient(app) as backend:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(backend))
        calls = []
        worker = service.Worker(lambda *args: calls.append(args))
        with TestClient(service.create_service(worker)) as agent:
            assert agent.post("/wake").status_code == 202
        assert not calls
        (attempt,) = backend.get(f"{CLAIM}/runs").json()
        assert attempt["thread_id"] == thread
        assert backend.get(CLAIM).json()["status"] == "in_progress"
        with app.state.store.connection() as connection:
            assert connection.scalar(select(swarm_runs.c.status)) == "stopped"
