import threading
import time
from contextlib import nullcontext

import pytest

from agent import main
from agent.tests.test_agent import StubModel, replies
from backend.finance import Finance
from backend.main import create_app
from backend.runs import Runs
from backend.schemas import DecisionInput, PaymentInput
from backend.storage import Store
from backend.tests.handler_session import SignedClient
from backend.tests.test_finance import decision


@pytest.mark.parametrize("amount", [420, 1250])
def test_background_activity(tmp_path, monkeypatch, amount):
    entered, release = threading.Event(), threading.Event()

    def runner(claim_id, thread_id):
        entered.set()
        assert release.wait(5)
        return main.run(claim_id, thread_id, StubModel(messages=iter(replies(amount))))

    app = create_app(tmp_path / "run.sqlite3", runner)
    with SignedClient(app) as client:
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
        status = "paused" if amount > 1000 else "completed"
        wait_for_run(client, status)
        run = client.get("/api/claims/CLM-1001/runs").json()[0]
        assert run["events"][-1]["kind"] == status
        assert "read_guidance_section" in {e["tool_name"] for e in run["events"]}
        assert client.post("/api/claims/CLM-1001/runs").status_code == 409
        assert (
            client.post(
                f"/api/runs/{thread}/events",
                json={"kind": "test", "tool_name": "", "text": "Too late"},
            ).status_code
            == 409
        )
    with SignedClient(app) as client:
        assert client.get("/api/claims/CLM-1001/runs").json()[0]["status"] == status


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
    with SignedClient(app) as client:
        assert client.post("/api/claims/CLM-1001/runs").status_code == 202
        for _ in range(100):
            if client.get("/api/claims/CLM-1001").json()["status"] == "needs_handler":
                break
            time.sleep(0.01)
        runs = client.get("/api/claims/CLM-1001/runs").json()
        assert len(runs) == 2
        assert all(r["status"] == "failed" for r in runs)
        assert "secret" not in runs[-1]["error"]
        assert client.post("/api/claims/CLM-1001/runs").status_code == 409
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
    with SignedClient(create_app(path, lambda c, t: "")) as client:
        saved = client.get("/api/claims/CLM-1001/runs").json()[0]
        assert saved["status"] == "interrupted"
        assert saved["events"][-1]["kind"] == "interrupted"
        assert client.get("/api/claims").json()[0]["status"] == "waiting"
        assert store.failures("CLM-1001") == 0
        assert previous.thread_id == runs.start("CLM-1001").thread_id
        assert len(runs.list("CLM-1001")) == 1


def test_committed_payment_survives_response_loss(tmp_path):
    def runner(claim_id, thread_id):
        finance = Finance(app.state.store)
        finance.decide(claim_id, DecisionInput.model_validate(decision()))
        finance.pay(claim_id, PaymentInput.model_validate({"amount": 420}))
        raise RuntimeError("Payment response lost")

    app = create_app(tmp_path / "paid.sqlite3", runner)
    with SignedClient(app) as client:
        client.post("/api/claims/CLM-1001/runs")
        wait_for_run(client, "completed")
        detail = client.get("/api/claims/CLM-1001").json()
        assert detail["decision"]["outcome"] == "approve"
        assert detail["payment"]["amount"] == 420
        assert client.post("/api/claims/CLM-1001/runs").status_code == 409


def test_agent_deadline_and_inactive_logging(tmp_path, monkeypatch):
    app = create_app(tmp_path / "deadline.sqlite3")
    with SignedClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        monkeypatch.setattr(main.time, "monotonic", lambda: 0)
        tools = main.tools_for("CLM-1001")
        monkeypatch.setattr(main.time, "monotonic", lambda: 181)
        with pytest.raises(TimeoutError):
            tools[0].invoke({})
        monkeypatch.undo()
        run = app.state.runs.start("CLM-1001")
        app.state.runs.finish(run.thread_id, "failed", "Stopped")
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        with pytest.raises(RuntimeError, match="Activity"):
            main.emit(run.thread_id, "tool_started", "read_claim", "Read")


def test_unfinished_payment_preserves_thread_and_budget(tmp_path):
    app = create_app(tmp_path / "payment-failure.sqlite3")
    with SignedClient(app) as client:
        runs = app.state.runs
        started = runs.start("CLM-1001")
        Finance(app.state.store).decide(
            "CLM-1001", DecisionInput.model_validate(decision())
        )
        result = runs.conclude("CLM-1001", started.thread_id, "Response lost")
        assert result.status == "interrupted"
        assert app.state.store.failures("CLM-1001") == 0
        assert result.thread_id == started.thread_id
        assert "CLM-1001" in client.get("/api/work").json()["claims"]
        recovery = client.post("/api/work/CLM-1001/activate").json()
        assert recovery["thread_id"] == started.thread_id
        detail = app.state.store.detail("CLM-1001")
        assert detail.decision is not None
        assert detail.payment is None


def test_signoff_activates_original_paused_attempt_before_notification(
    tmp_path, monkeypatch
):
    app = create_app(tmp_path / "waiting.sqlite3")
    with SignedClient(app) as client:
        runs = app.state.runs
        cid = "CLM-1001"
        thread = runs.start(cid).thread_id
        Finance(app.state.store).decide(
            cid, DecisionInput.model_validate(decision(1250))
        )
        assert runs.conclude(cid, thread, "").status == "paused"
        observed = []
        monkeypatch.setattr(
            "backend.dispatch.notify", lambda _: observed.extend(runs.list(cid))
        )
        client.post(f"/api/claims/{cid}/sign-off", json={}).raise_for_status()
        assert [(run.thread_id, run.status) for run in observed] == [
            (thread, "running")
        ]
        assert app.state.store.detail(cid).payment is None
