from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app

CLAIM = "/api/claims/CLM-1001"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "finance.sqlite3")) as client:
        yield client


def decision(amount: float = 420, outcome: str = "approve"):
    return {
        "outcome": outcome,
        "payout_amount": amount,
        "explanation": (
            "The covered delay meets the waiting period. Receipts support the expenses."
        ),
        "calculation": f"Eligible expenses = ${amount}",
        "policy_references": ["3", "10"],
        "manual_references": ["4", "6"],
    }


@pytest.mark.parametrize("amount", [999.99, 1000, 1000.01, 1250])
def test_boundary_sign_off_and_payment(client, amount):
    assert client.post(f"{CLAIM}/decision", json=decision(amount)).status_code == 200
    response = client.post(f"{CLAIM}/payment", json={"amount": amount})
    if amount > 1000:
        assert response.status_code == 403
        assert client.post(f"{CLAIM}/payment", json={"amount": 1}).status_code == 403
        assert client.get(CLAIM).json()["status"] == "waiting_sign_off"
        assert (
            client.post(f"{CLAIM}/sign-off", json={"handler_id": "missing"}).status_code
            == 404
        )
        assert (
            client.post(f"{CLAIM}/sign-off", json={"handler_id": "HND-01"}).status_code
            == 200
        )
        assert client.get(CLAIM).json()["payment"] is None
        assert (
            client.post(f"{CLAIM}/sign-off", json={"handler_id": "HND-01"}).status_code
            == 409
        )
        response = client.post(f"{CLAIM}/payment", json={"amount": amount})
    assert response.status_code == 200
    assert response.json()["amount_cents"] == round(amount * 100)
    assert response.json()["simulated"] is True
    assert client.get(CLAIM).json()["status"] == "decided"
    assert client.post(f"{CLAIM}/payment", json={"amount": amount}).status_code == 409
    assert client.post(f"{CLAIM}/decision", json=decision()).status_code == 409
    assert client.get("/api/claims").json()[0]["payment_status"] == "paid"
    assert client.get("/api/claims").json()[0]["payout_amount"] == amount


@pytest.mark.parametrize("outcome", ["decline", "refer"])
def test_nonpayable_outcomes(client, outcome):
    assert (
        client.post(f"{CLAIM}/decision", json=decision(0, outcome)).status_code == 200
    )
    assert client.get(CLAIM).json()["status"] == "decided"
    assert client.post(f"{CLAIM}/payment", json={"amount": 1}).status_code == 409
    assert (
        client.post(f"{CLAIM}/sign-off", json={"handler_id": "HND-01"}).status_code
        == 409
    )


def test_invalid_and_concurrent_payment(client):
    assert client.post(f"{CLAIM}/payment", json={"amount": 1}).status_code == 409
    for payload in [
        decision(0),
        decision(1, "refer"),
        decision(1.001),
        {**decision(), "policy_references": ["99"]},
    ]:
        assert client.post(f"{CLAIM}/decision", json=payload).status_code == 422
    client.post(f"{CLAIM}/decision", json=decision())
    for amount in [0, -1, 1.001, "NaN"]:
        assert (
            client.post(f"{CLAIM}/payment", json={"amount": amount}).status_code == 422
        )
    assert client.post(f"{CLAIM}/payment", json={"amount": 419}).status_code == 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(
            pool.map(
                lambda _: (
                    client.post(f"{CLAIM}/payment", json={"amount": 420}).status_code
                ),
                range(2),
            )
        )
    assert sorted(statuses) == [200, 409]


def test_ineligible_handler_and_persistence(tmp_path):
    app = create_app(tmp_path / "persist.sqlite3")
    with TestClient(app) as client:
        client.post(f"{CLAIM}/decision", json=decision(1250))
        store = app.state.store
        handler = store.get("handlers", "HND-02")
        handler["can_approve_over_limit"] = False
        with store.connection() as connection:
            store.update(connection, "handlers", "HND-02", handler)
        assert (
            client.post(f"{CLAIM}/sign-off", json={"handler_id": "HND-02"}).status_code
            == 403
        )
        client.post(f"{CLAIM}/sign-off", json={"handler_id": "HND-01"})
        client.post(f"{CLAIM}/payment", json={"amount": 1250})
    with TestClient(app) as client:
        detail = client.get(CLAIM).json()
        assert detail["decision"]["payout_cents"] == 125000
        assert detail["sign_off"]["handler_id"] == "HND-01"
        assert detail["payment"]["amount_cents"] == 125000
