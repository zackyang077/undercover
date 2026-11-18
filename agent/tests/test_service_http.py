"""Exercise the real claims API, agent HTTP routes and CLI without a provider."""

import runpy
import sys
from contextlib import nullcontext
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import main, service
from agent.tests.test_agent import StubModel
from backend import dispatch
from backend.dispatch import notify as send_notification
from backend.main import create_app
from backend.tests.test_finance import decision

CLAIM_ID = "CLM-1001"
CLAIM = f"/api/claims/{CLAIM_ID}"
RESUME = f"/claims/{CLAIM_ID}/resume"


@pytest.fixture
def backend(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / "service.db")) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        yield client


def test_notifications_and_wake_respond_while_payment_is_blocked(backend, monkeypatch):
    entered, allow_payment = Event(), Event()
    calls = []
    notified = []
    backend.post(f"{CLAIM}/decision", json=decision(1250)).raise_for_status()

    def pay_when_released(claim_id, thread):
        calls.append(thread)
        entered.set()
        if not allow_payment.wait(timeout=5):
            raise RuntimeError("Payment was still blocked")
        return main.run(claim_id, thread, StubModel(messages=iter([])))

    worker = service.Worker(pay_when_released)
    with TestClient(service.create_service(worker)) as agent:

        def post_to_agent(url, **kwargs):
            assert url == f"http://agent.test{RESUME}"
            # A new backend request must already see the committed sign-off.
            notified.append(backend.get(CLAIM).json()["sign_off"])
            return agent.post(RESUME)

        monkeypatch.setenv("UNDERCOVER_AGENT", "http://agent.test/")
        monkeypatch.setattr(dispatch, "notify", send_notification)
        monkeypatch.setattr(dispatch.httpx, "post", post_to_agent)
        try:
            assert agent.get("/health").json() == {"status": "ok"}
            signed = backend.post(f"{CLAIM}/sign-off", json={"handler_id": "HND-01"})
            signed.raise_for_status()
            assert notified == [signed.json()]
            assert entered.wait(timeout=5)
            assert agent.post(RESUME).status_code == 202
            assert agent.post("/wake").status_code == 202
            pending = backend.get(CLAIM).json()
            assert pending["status"] == "in_progress"
            assert pending["payment"] is None
        finally:
            allow_payment.set()
    assert len(calls) == 1
    paid = backend.get(CLAIM).json()["payment"]
    assert paid["amount"] == 1250
    assert paid["sign_off"] == signed.json()


def test_unreachable_service_keeps_saved_signoff(backend, monkeypatch):
    seen = []

    def offline(url, **kwargs):
        seen.append(url)
        raise httpx.ConnectError("Agent service is down")

    monkeypatch.delenv("UNDERCOVER_AGENT", raising=False)
    monkeypatch.setattr(dispatch, "notify", send_notification)
    monkeypatch.setattr(dispatch.httpx, "post", offline)
    backend.post(f"{CLAIM}/decision", json=decision(1250)).raise_for_status()
    assert backend.post(f"{CLAIM}/sign-off", json={"handler_id": "missing"}).is_error
    assert not seen
    signed = backend.post(f"{CLAIM}/sign-off", json={"handler_id": "HND-01"})
    signed.raise_for_status()
    assert seen == [f"http://127.0.0.1:8002{RESUME}"]
    detail = backend.get(CLAIM).json()
    assert detail["sign_off"] == signed.json()
    assert detail["status"] == "in_progress"
    assert detail["payment"] is None


@pytest.mark.parametrize("args", [[], ["--limit", "2"], ["--claim", CLAIM_ID]])
def test_module_starts_work_and_wakes_service(backend, monkeypatch, capsys, args):
    notifications = []
    monkeypatch.setattr(dispatch, "notify", notifications.append)
    monkeypatch.setattr(sys, "argv", ["agent", *args])
    runpy.run_module("agent", run_name="__main__")
    reported = capsys.readouterr().out.strip()
    if "--claim" in args:
        (run,) = backend.get(f"{CLAIM}/runs").json()
        assert reported == run["thread_id"]
    else:
        batch = backend.get("/api/queue/runs/current").json()
        assert reported == batch["queue_run_id"]
        assert batch["target"] == (2 if args else 5)
    assert notifications == ["/wake"]
