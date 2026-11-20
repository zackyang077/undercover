from unittest.mock import Mock

import httpx
import pytest

from backend import agent_service
from backend.main import create_app
from backend.tests.handler_session import SignedClient
from backend.tests.test_finance import decision
from backend.tests.test_swarm import dispatch


@pytest.fixture
def client(tmp_path):
    with SignedClient(create_app(tmp_path / "dispatch.sqlite3")) as client:
        yield client


@pytest.mark.parametrize(
    "payload",
    [{}, {"concurrency": 0}, {"concurrency": 26}, {"concurrency": "3"}],
)
def test_start_validates_concurrency_before_forwarding(client, monkeypatch, payload):
    forward = Mock()
    monkeypatch.setattr(agent_service.httpx, "post", forward)
    assert client.post("/api/dispatch", json=payload).status_code == 422
    forward.assert_not_called()
    assert client.get("/api/dispatch/current").json() is None


def test_accepted_start_forwards_n_without_creating_a_run(client, monkeypatch):
    forward = Mock(return_value=httpx.Response(202, json={"run_id": "accepted-id"}))
    monkeypatch.setenv("UNDERCOVER_AGENT", "http://agent.test/")
    monkeypatch.setattr(agent_service.httpx, "post", forward)
    response = client.post("/api/dispatch", json={"concurrency": 25})
    assert response.status_code == 202
    assert response.json() == {"run_id": "accepted-id"}
    forward.assert_called_once_with(
        "http://agent.test/dispatch", json={"concurrency": 25}, timeout=2
    )
    assert client.get("/api/dispatch/accepted-id").status_code == 404
    assert client.get("/api/dispatch/current").json() is None


def test_start_preserves_conflict_and_service_unavailable(client, monkeypatch):
    conflict = {"detail": "A dispatch or evaluation is already running"}
    forward = Mock(
        side_effect=[httpx.Response(409, json=conflict), httpx.ConnectError("Offline")]
    )
    monkeypatch.setattr(agent_service.httpx, "post", forward)
    response = client.post("/api/dispatch", json={"concurrency": 1})
    assert response.status_code == 409
    assert response.json() == conflict
    unavailable = client.post("/api/dispatch", json={"concurrency": 3})
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "The agent service is unavailable"}
    assert client.get("/api/dispatch/current").json() is None


def activate(client, run_path: str, claim_id: str) -> str:
    response = client.post(f"{run_path}/claims/{claim_id}/activate")
    assert response.status_code == 200
    return response.json()["thread_id"]


def finish(client, run_path: str, claim_id: str, thread: str, error: str = ""):
    response = client.post(
        f"{run_path}/claims/{claim_id}/{thread}/finish", json={"error": error}
    )
    assert response.status_code == 200
    return response.json()


def test_dispatch_summary_uses_saved_records_and_freezes_on_close(client):
    run_id = "summary"
    run = f"/api/swarm/runs/{run_id}"
    held, declined, referred, handoff, remaining = [f"CLM-100{x}" for x in range(1, 6)]
    assert dispatch(client, run_id).status_code == 201
    assert client.get("/api/dispatch/current").json()["summary"] is None
    for claim, amount, outcome in (
        (held, 1250, "approve"),
        (declined, 0, "decline"),
        (referred, 0, "refer"),
    ):
        thread = activate(client, run, claim)
        saved = client.post(
            f"/api/claims/{claim}/decision", json=decision(amount, outcome)
        )
        assert saved.status_code == 200
        finish(client, run, claim, thread)
    failed = activate(client, run, handoff)
    assert finish(client, run, handoff, failed, "stub error")["retry"]
    failed_again = activate(client, run, handoff)
    assert not finish(client, run, handoff, failed_again, "stub error")["retry"]
    thread = activate(client, run, remaining)
    reports = f"/api/runs/{thread}"
    assert (
        client.post(
            f"{reports}/usage",
            json={
                "claim_id": remaining,
                "run_id": run_id,
                "call_id": "provider-call",
                "usage": {"cost": 0.0153, "total_tokens": 42},
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{reports}/work-segments",
            json={
                "claim_id": remaining,
                "run_id": run_id,
                "segment_id": "first-segment",
                "started_at": "2026-11-19T09:00:00.000Z",
                "finished_at": "2026-11-19T09:00:00.300Z",
                "elapsed_ms": 300,
            },
        ).status_code
        == 200
    )
    closed = client.post(
        f"{run}/close", json={"status": "completed", "error": "", "dispositions": {}}
    )
    assert closed.status_code == 200
    snapshot = client.get(f"/api/dispatch/{run_id}").json()
    assert snapshot["status"] == "completed"
    assert snapshot["summary"] == {
        "approved": 1,
        "declined": 1,
        "referred": 1,
        "waiting_sign_off": [{"claim_id": held, "payout_amount": 1250}],
        "needs_handler": [handoff],
        "in_progress": [remaining],
    }
    assert snapshot["model_cost"] == "0.0153"
    assert snapshot["model_cost_complete"] is True
    assert snapshot["elapsed_ms"] == 300

    sign = client.post(f"/api/claims/{held}/sign-off", json={})
    assert sign.status_code == 200
    payment = client.post(f"/api/claims/{held}/payment", json={"amount": 1250})
    assert payment.status_code == 200
    assert client.get("/api/dispatch/current").json() == snapshot
