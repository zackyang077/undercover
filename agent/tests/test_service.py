from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

from fastapi.testclient import TestClient

from agent import main, service
from agent.tests.test_agent import StubModel, replies
from backend.main import create_app
from backend.queue_runs import CappedClaims
from backend.tests.test_finance import decision


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
