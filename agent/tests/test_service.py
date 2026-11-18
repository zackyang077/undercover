from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Event

import pytest
from fastapi.testclient import TestClient
from langgraph.graph.state import CompiledStateGraph

from agent import main, service
from agent.checkpoints import open_saver
from agent.tests.test_agent import StubModel, replies
from backend.main import create_app
from backend.queue_runs import CappedClaims
from backend.tests.test_finance import decision


@pytest.mark.parametrize("payment_fails", [False, True])
def test_old_queue_report_cannot_interrupt_signed_thread_during_resume(
    tmp_path, monkeypatch, payment_fails
):
    app = create_app(tmp_path / "pause-report-race.db")
    cid = "CLM-1001"
    with TestClient(app) as api:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(api))
        monkeypatch.setattr(
            main, "model", lambda: StubModel(messages=iter(replies(1250)))
        )
        queue = app.state.queue
        batch = queue.start(CappedClaims(mode="capped", max_claims=1))
        thread = queue.admit(batch.queue_run_id, cid)["thread_id"]
        worker = service.Worker()
        assert worker.claim(cid, thread) == "Waiting for sign-off"
        api.post(
            f"/api/claims/{cid}/sign-off", json={"handler_id": "HND-01"}
        ).raise_for_status()

        resuming, release = Event(), Event()
        emit = main.emit

        def suspend_before_event(thread_id, kind, step, message):
            if step == "sign_off":
                resuming.set()
                assert release.wait(timeout=5)
            return emit(thread_id, kind, step, message)

        monkeypatch.setattr(main, "emit", suspend_before_event)
        if payment_fails:
            request = main.request

            def fail_payment(method, path, body=None):
                if path.endswith("/payment") and method == "POST":
                    raise RuntimeError("Simulated payment failure")
                return request(method, path, body)

            monkeypatch.setattr(main, "request", fail_payment)
        with ThreadPoolExecutor(max_workers=1) as pool:
            resume = pool.submit(worker.resume, cid)
            try:
                assert resuming.wait(timeout=5)
                # The queue reports that the pre-sign-off graph invocation returned.
                queue.finish_attempt(batch.queue_run_id, thread, "")
            finally:
                release.set()
            resume.result(timeout=5)
        record = api.get(f"/api/claims/{cid}").json()
        (attempt,) = app.state.runs.list(cid)
        if payment_fails:
            assert record["payment"] is None
            assert attempt.status == "interrupted"
            assert attempt.error == "Simulated payment failure"
            assert queue.check(batch.queue_run_id)["resumable"] == [cid]
            return
        assert record["payment"]["amount"] == 1250
        assert attempt.status == "completed"
        assert "interrupted" not in {event.kind for event in attempt.events}
        assert queue.check(batch.queue_run_id)["complete"]


@pytest.mark.parametrize("warm_thread", [True, False])
def test_signed_checkpoint_fast_path_requires_matching_cached_thread(
    tmp_path, monkeypatch, warm_thread
):
    app = create_app(tmp_path / "signed-thread.sqlite3")
    cid = "CLM-1001"
    with TestClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        stub = StubModel(messages=iter(replies(1250)))
        monkeypatch.setattr(main, "model", lambda: stub)
        worker = service.Worker()
        runs = app.state.runs
        thread = runs.start(cid).thread_id
        assert worker.claim(cid, thread) == "Waiting for sign-off"
        assert runs.conclude(cid, thread, "").status == "paused"
        with open_saver() as saver:
            history = list(saver.list({"configurable": {"thread_id": thread}}))
            assert history[0].checkpoint["channel_values"]["messages"] == []
            assert any(
                cp.checkpoint["channel_values"].get("messages") for cp in history
            )
        signed_url = f"/api/work/{cid}/signed-thread"
        assert client.get(signed_url).json() is None
        worker.resume(cid)
        assert client.get(f"/api/claims/{cid}").json()["payment"] is None
        signed = client.post(
            f"/api/claims/{cid}/sign-off", json={"handler_id": "HND-01"}
        )
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
            worker.waiting_threads[cid] = "stale-thread"
        worker.resume(cid)
        assert calls[0] == signed_url
        assert (f"/api/work/{cid}/activate" in calls) == (not warm_thread)
        paid = client.get(f"/api/claims/{cid}").json()["payment"]
        assert paid["amount"] == 1250
        assert paid["sign_off"] == signed.json()
        assert client.get(signed_url).json() is None


@pytest.mark.parametrize("close_status", ["completed", "failed"])
def test_unpaid_attempt_keeps_queue_until_original_thread_pays(
    tmp_path, monkeypatch, close_status
):
    app = create_app(tmp_path / "interrupted-payment.sqlite3")
    with TestClient(app) as api:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(api))
        queue = app.state.queue
        batch = queue.start(CappedClaims(mode="capped", max_claims=1))
        cid = "CLM-1001"
        thread = queue.admit(batch.queue_run_id, cid)["thread_id"]
        api.post(f"/api/claims/{cid}/decision", json=decision(420)).raise_for_status()
        queue.finish_attempt(batch.queue_run_id, thread, "Payment request interrupted")
        check = queue.check(batch.queue_run_id)
        assert check["resumable"] == [cid]
        assert not check["complete"]
        queue.finish(batch.queue_run_id, close_status)
        assert queue.current().status == "interrupted"

        if close_status == "failed":
            continued = queue.start(CappedClaims(mode="capped", max_claims=5))
            assert (continued.queue_run_id, continued.target) == (batch.queue_run_id, 1)
        visited = []

        def claim_runner(claim_id, thread_id):
            visited.append((claim_id, thread_id))
            return main.run(claim_id, thread_id, StubModel(messages=iter([])))

        def queue_runner(queue_id, thread_id, *, claim_runner):
            assert (queue_id, thread_id) == (batch.queue_run_id, batch.thread_id)
            assert queue.check(queue_id)["complete"]
            return "Paid the remaining approval."

        service.Worker(claim_runner, queue_runner).drain()
        assert visited == [(cid, thread)]
        assert queue.current().status == "completed"
        assert len(api.get(f"/api/claims/{cid}/runs").json()) == 1
        assert api.get(f"/api/claims/{cid}").json()["payment"]["amount"] == 420


def test_held_approval_allows_batch_to_finish(tmp_path):
    app = create_app(tmp_path / "held-payment.sqlite3")
    with TestClient(app) as api:
        queue = app.state.queue
        batch = queue.start(CappedClaims(mode="capped", max_claims=1))
        cid = "CLM-1001"
        thread = queue.admit(batch.queue_run_id, cid)["thread_id"]
        api.post(f"/api/claims/{cid}/decision", json=decision(1250)).raise_for_status()
        queue.finish_attempt(batch.queue_run_id, thread, "")
        assert queue.check(batch.queue_run_id)["complete"]
        queue.finish(batch.queue_run_id, "completed")
        assert queue.current().status == "completed"
        assert api.get(f"/api/claims/{cid}").json()["status"] == "waiting_sign_off"


def test_service_recovers_queue_with_original_threads_and_cap(tmp_path, monkeypatch):
    path = tmp_path / "recovery.db"
    first = create_app(path)
    with TestClient(first) as api:
        queue = first.state.queue
        batch = queue.start(CappedClaims(mode="capped", max_claims=5))
        claim_one, claim_two = "CLM-1001", "CLM-1002"
        prev = queue.admit(batch.queue_run_id, claim_one)
        api.post(f"/api/claims/{claim_one}/decision", json=decision(0, "refer"))
        queue.finish_attempt(batch.queue_run_id, prev["thread_id"], "")
        interrupted = queue.admit(batch.queue_run_id, claim_two)["thread_id"]

    working = []
    with TestClient(create_app(path)) as api:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(api))

        def claim_runner(claim_id, thread_id):
            working.append((claim_id, thread_id))
            api.post(
                f"/api/claims/{claim_id}/decision", json=decision(0, "refer")
            ).raise_for_status()
            return "Referred"

        def queue_runner(queue_id, thread_id, *, claim_runner):
            current = api.get("/api/queue/runs/current").json()
            assert current["admitted"] == [claim_one, claim_two]
            assert current["items"][1]["status"] == "completed"
            for number in range(3, 6):
                claim_id = f"CLM-{1000 + number}"
                prefix = f"/api/queue/runs/{queue_id}"
                assigned = api.post(f"{prefix}/claims/{claim_id}/attempts").json()[
                    "thread_id"
                ]
                claim_runner(claim_id, assigned)
                api.post(
                    f"{prefix}/attempts/{assigned}/finish", json={"error": ""}
                ).raise_for_status()
            return "Five completed."

        service.Worker(claim_runner, queue_runner).drain()
        current = api.get("/api/queue/runs/current").json()
        assert current["queue_run_id"] == batch.queue_run_id
        assert current["status"] == "completed"
        assert len(current["admitted"]) == 5
        assert (claim_two, interrupted) == working[0]
        assert len(api.get(f"/api/claims/{claim_two}/runs").json()) == 1
        assert len(working) == 4


def test_restart_after_missed_signoff_and_duplicate_notifications(
    tmp_path, monkeypatch
):
    path = tmp_path / "signed.db"
    cid = "CLM-1001"
    app = create_app(path)
    with TestClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        runs = app.state.runs
        thread = runs.start(cid).thread_id
        main.run(cid, thread, StubModel(messages=iter(replies(1250))))
        assert runs.conclude(cid, thread, "").status == "paused"
        result = client.post(
            f"/api/claims/{cid}/sign-off", json={"handler_id": "HND-01"}
        )
        assert result.is_success
        assert client.get(f"/api/claims/{cid}").json()["status"] == "in_progress"

    app = create_app(path)
    with TestClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        worker = service.Worker(
            lambda c, t: main.run(c, t, StubModel(messages=iter([])))
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(worker.resume, cid), pool.submit(worker.drain)]
            for job in jobs:
                job.result(timeout=10)
        record = client.get(f"/api/claims/{cid}").json()
        assert record["payment"]["amount"] == 1250
        assert record["payment"]["sign_off"] == record["sign_off"]
        assert client.get(f"/api/claims/{cid}/runs").json()[0]["thread_id"] == thread
        assert len(app.state.store.all("payments")) == 1
