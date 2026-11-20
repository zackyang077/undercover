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
from backend.tests.handler_session import SignedClient, token
from backend.tests.test_finance import decision

CLAIM_ID = "CLM-1001"
CLAIM = f"/api/claims/{CLAIM_ID}"
RESUME = f"/claims/{CLAIM_ID}/resume"


@pytest.fixture
def backend(tmp_path, monkeypatch):
    with SignedClient(create_app(tmp_path / "service.db")) as client:
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
            signed = backend.post(f"{CLAIM}/sign-off", json={})
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
    signed = backend.post(f"{CLAIM}/sign-off", json={})
    signed.raise_for_status()
    assert seen == [f"http://127.0.0.1:8002{RESUME}"]
    detail = backend.get(CLAIM).json()
    assert detail["sign_off"] == signed.json()
    assert detail["status"] == "in_progress"
    assert detail["payment"] is None


def test_module_starts_work_and_wakes_service(backend, monkeypatch, capsys):
    notifications = []
    monkeypatch.setenv("HANDLER_ACCESS_TOKEN", token())
    monkeypatch.setattr(dispatch, "notify", notifications.append)
    monkeypatch.setattr(sys, "argv", ["agent", CLAIM_ID])
    runpy.run_module("agent", run_name="__main__")
    reported = capsys.readouterr().out.strip()
    (run,) = backend.get(f"{CLAIM}/runs").json()
    assert reported == run["thread_id"]
    assert notifications == ["/wake"]


def test_module_requires_claim_or_serve(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["agent"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("agent", run_name="__main__")
    assert exit_info.value.code == 2


@pytest.mark.parametrize("kind", ["dispatch", "evaluation"])
def test_accepts_before_backend_creation_and_reserves_one_run(kind, monkeypatch):
    entering, allow_create = Event(), Event()
    recorded = []
    models = []
    cleared = []

    def model(settings):
        models.append(settings)
        return StubModel(messages=iter([]))

    def api(method, path, body=None):
        if path == "/api/swarm/runs/recover":
            return {"evaluation_threads": ["stopped-evaluation-thread"]}
        if path == "/api/work":
            return {"claims": []}
        recorded.append((method, path, body))
        if path == "/api/swarm/runs":
            entering.set()
            assert allow_create.wait(timeout=5)
            assert body is not None
            return {"run_id": body["run_id"]}
        if path.endswith("/work"):
            return []
        return {
            "status": "recorded",
            "evaluation_threads": ["closed-evaluation-thread"]
            if kind == "evaluation"
            else [],
        }

    monkeypatch.setattr(service, "api", api)
    monkeypatch.setattr(service, "clear_threads", cleared.extend)
    monkeypatch.setattr(main, "model", model)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    dispatch_request = ("/dispatch", {"concurrency": 3})
    evaluation_request = (
        "/evaluations",
        {"dataset": "held_out", "size": "tiny", "concurrency": 5},
    )
    if kind == "dispatch":
        first, second = dispatch_request, evaluation_request
        expected_model, effort = main.DEFAULT_MODEL, "low"
    else:
        first, second = evaluation_request, dispatch_request
        expected_model, effort = "openrouter/free", None
        monkeypatch.setenv("MODEL_NAME", expected_model)

    with TestClient(service.create_service()) as agent:
        try:
            invalid = {**first[1], "concurrency": 26}
            assert agent.post(first[0], json=invalid).status_code == 422
            accepted = agent.post(first[0], json=first[1])
            assert accepted.status_code == 202
            run_id = accepted.json()["run_id"]
            assert entering.wait(timeout=5)
            refused = agent.post(second[0], json=second[1])
            assert refused.status_code == 409
            monkeypatch.setenv("MODEL_NAME", "later-config")
        finally:
            allow_create.set()

    assert recorded == [
        (
            "POST",
            "/api/swarm/runs",
            {
                "run_id": run_id,
                "kind": kind,
                "concurrency": first[1]["concurrency"],
                "model": expected_model,
                "reasoning_effort": effort,
                "dataset": first[1].get("dataset"),
                "size": first[1].get("size"),
            },
        ),
        ("GET", f"/api/swarm/runs/{run_id}/work", None),
        (
            "POST",
            f"/api/swarm/runs/{run_id}/close",
            {"status": "completed", "error": "", "dispositions": {}},
        ),
    ]
    assert [(setting.model_name, setting.reasoning_effort) for setting in models] == [
        (expected_model, effort)
    ]
    assert cleared == (
        ["stopped-evaluation-thread", "closed-evaluation-thread"]
        if kind == "evaluation"
        else ["stopped-evaluation-thread"]
    )


def test_start_requires_recovery_before_accepting_work(monkeypatch):
    def api(*args):
        assert args == ("POST", "/api/swarm/runs/recover")
        raise httpx.ConnectError("Backend is still starting")

    monkeypatch.setattr(service, "api", api)
    with TestClient(service.create_service()) as agent:
        response = agent.post("/dispatch", json={"concurrency": 3})
    assert response.status_code == 503
