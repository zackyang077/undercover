import time

import jwt
import pytest
from fastapi.testclient import TestClient
from httpx import Request, Response

from backend import agent_service
from backend.main import create_app
from backend.tests.handler_session import DANA, MARCUS, PRIYA, bearer
from backend.tests.test_finance import decision

CLAIM = "/api/claims/CLM-1001"
ACTIONS = [
    ("/api/dispatch", {"concurrency": 2}),
    ("/api/evaluations", {"concurrency": 1, "dataset": "evaluation", "size": "tiny"}),
    (f"{CLAIM}/runs", {}),
    (f"{CLAIM}/sign-off", {}),
]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "auth.db")) as client:
        yield client


@pytest.mark.parametrize(("route", "body"), ACTIONS)
@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"Authorization": "Bearer invalid"}, 401),
        (bearer(exp=int(time.time()) - 60), 401),
        (bearer(iss="https://different-project.test/auth/v1"), 401),
        (bearer(aud="other"), 401),
        (bearer("other@undercover.example"), 403),
    ],
)
def test_action_requires_valid_handler(client, route, body, headers, status):
    assert client.post(route, json=body, headers=headers).status_code == status
    assert client.get(f"{CLAIM}/runs").json() == []
    assert client.get(CLAIM).json()["sign_off"] is None


@pytest.mark.parametrize("email", [MARCUS, PRIYA])
def test_handler_can_dispatch_and_evaluate_but_cannot_sign_for_dana(
    client, monkeypatch, email
):

    monkeypatch.setattr(
        agent_service.httpx,
        "post",
        lambda url, **kw: Response(
            202, json={"run_id": "test"}, request=Request("POST", url)
        ),
    )
    headers = bearer(email)
    profile = client.get("/api/me", headers=headers).json()
    assert profile["email"] == email
    assert profile["can_approve_over_limit"] is False
    for route, body in ACTIONS[:2]:
        assert client.post(route, json=body, headers=headers).status_code == 202
    client.post(f"{CLAIM}/decision", json=decision(1500)).raise_for_status()
    assert client.post(f"{CLAIM}/sign-off", json={}, headers=headers).status_code == 403
    assert (
        client.post(
            f"{CLAIM}/sign-off", json={"handler_id": "HND-01"}, headers=headers
        ).status_code
        == 403
    )
    assert client.get(CLAIM).json()["sign_off"] is None
    dana = bearer(DANA)
    assert client.get("/api/me", headers=dana).json()["can_approve_over_limit"] is True
    assert (
        client.post(f"{CLAIM}/sign-off", json={}, headers=dana).json()["handler_id"]
        == "HND-01"
    )


def test_broken_jwks_service_returns_unavailable(client, monkeypatch):
    def unavailable(*args):
        raise jwt.PyJWKClientConnectionError("test JWKS unavailable")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", unavailable)
    assert client.get("/api/me", headers=bearer()).status_code == 503
