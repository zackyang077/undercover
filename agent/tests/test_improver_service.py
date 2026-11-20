from threading import Event

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from agent import improver_api, service

REVIEW = {
    "proposal_id": "proposal",
    "choice": "approve",
    "reason": "",
    "handler_id": "HND-01",
}


@pytest.mark.parametrize("choice", ["approve", "reject"])
def test_round_admission_rejects_stale_and_busy_reviews_before_recording(
    monkeypatch, choice
):
    backend, posted = [], []
    acquired, release = Event(), Event()

    def regular_api(method, path, body=None):
        if path == "/api/work":
            return {"claims": []}
        if path == "/api/agent/configuration/current":
            return {
                "version": 1,
                "model": "fake",
                "reasoning_effort": "low",
                "instructions": "Review.",
            }
        return {}

    def round_api(method, path, body=None):
        backend.append((method, path, body))
        if path.endswith("/review") and body and body["proposal_id"] != "proposal":
            raise HTTPException(409, "This proposal has already been reviewed")
        return {"id": "round"}

    def wait_for_finish(*args):
        acquired.set()
        assert release.wait(5)

    monkeypatch.setattr(service, "api", regular_api)
    monkeypatch.setattr(improver_api, "request", round_api)
    worker = service.Worker()
    monkeypatch.setattr(worker, "run_swarm", wait_for_finish)
    monkeypatch.setattr(worker, "improve", lambda client, review: posted.append(review))
    with TestClient(service.create_service(worker)) as client:
        assert client.post("/dispatch", json={"concurrency": 1}).status_code == 202
        try:
            assert acquired.wait(5)
            blocked = client.post(
                "/improvement/round/review",
                json={**REVIEW, "choice": choice, "reason": "Check wording."},
            )
            assert blocked.status_code == 409
            assert "dispatch" in blocked.json()["detail"]
            assert backend == []
        finally:
            release.set()
    with TestClient(service.create_service(worker)) as client:
        stale = client.post(
            "/improvement/round/review", json={**REVIEW, "proposal_id": "stale"}
        )
        assert stale.status_code == 409
        assert posted == []
        accepted = client.post("/improvement/round/review", json=REVIEW)
        assert accepted.status_code == 202
    assert posted == [REVIEW]
    assert backend[-1] == ("POST", "/api/improver/rounds/round/review", REVIEW)


def test_unavailable_start_releases_admission(monkeypatch):
    worker = service.Worker()
    tasks = service.ServiceTasks(worker)
    tasks.ready.set()
    monkeypatch.setattr(
        improver_api,
        "request",
        lambda *_: (_ for _ in ()).throw(HTTPException(503, "Offline")),
    )
    with pytest.raises(HTTPException):
        tasks.improvement({"handler_id": "HND-01"}, None, service.BackgroundTasks())
    tasks.reserve("dispatch")
    tasks.release()


def test_active_round_excludes_another_start_and_both_job_types(monkeypatch):
    entered, release = Event(), Event()
    writes = []

    def record(method, path, request):
        writes.append(request)
        return {"id": "round"}

    def pause(client, decision):
        entered.set()
        assert release.wait(5)

    def backend(method, path, body=None):
        return {"claims": []} if path == "/api/work" else {}

    monkeypatch.setattr(service, "api", backend)
    monkeypatch.setattr(improver_api, "request", record)
    worker = service.Worker()
    monkeypatch.setattr(worker, "improve", pause)
    with TestClient(service.create_service(worker)) as agent:
        body = {"handler_id": "HND-01"}
        assert agent.post("/improvement/start", json=body).status_code == 202
        try:
            assert entered.wait(5)
            for route, request in [
                ("/improvement/start", body),
                ("/dispatch", {"concurrency": 1}),
                (
                    "/evaluations",
                    {"concurrency": 1, "dataset": "held_out", "size": "full"},
                ),
            ]:
                response = agent.post(route, json=request)
                assert response.status_code == 409
                assert "round" in response.json()["detail"]
        finally:
            release.set()
    assert writes == [body]
