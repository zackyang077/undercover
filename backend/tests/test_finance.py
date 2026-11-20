from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event

from backend.main import create_app
from backend.tests.handler_session import MARCUS, SignedClient, bearer

CLAIM = "/api/claims/CLM-1001"


@pytest.fixture
def client(tmp_path):
    with SignedClient(create_app(tmp_path / "finance.sqlite3")) as client:
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
            == 422
        )
        assert client.post(f"{CLAIM}/sign-off", json={}).status_code == 200
        signed = client.get(CLAIM).json()
        assert signed["payment"] is None
        assert signed["status"] == "in_progress"
        assert client.get("/api/claims").json()[0]["status"] == "in_progress"
        assert client.post(f"{CLAIM}/sign-off", json={}).status_code == 409
        response = client.post(f"{CLAIM}/payment", json={"amount": amount})
    assert response.status_code == 200
    detail = response.json()
    assert detail == client.get(CLAIM).json()
    assert detail["payment"]["amount_cents"] == round(amount * 100)
    assert detail["payment"]["simulated"] is True
    assert detail["payment"]["sign_off"] == detail["sign_off"]
    assert client.get(CLAIM).json()["status"] == "decided"
    replayed = client.post(f"{CLAIM}/payment", json={"amount": amount})
    assert replayed.status_code == 200
    assert replayed.json() == detail
    assert client.post(f"{CLAIM}/payment", json={"amount": 1}).status_code == 409
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
    assert client.post(f"{CLAIM}/sign-off", json={}).status_code == 409


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
        responses = list(
            pool.map(
                lambda _: client.post(f"{CLAIM}/payment", json={"amount": 420}),
                range(2),
            )
        )
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json() == client.get(CLAIM).json()
    assert len(client.app.state.store.all("payments")) == 1


def test_ineligible_handler_and_persistence(tmp_path):
    app = create_app(tmp_path / "persist.sqlite3")
    with SignedClient(app) as client:
        client.post(f"{CLAIM}/decision", json=decision(1250))
        store = app.state.store
        handler = store.get("handlers", "HND-02")
        handler["can_approve_over_limit"] = False
        with store.connection() as connection:
            store.update(connection, "handlers", "HND-02", handler)
        assert (
            client.post(
                f"{CLAIM}/sign-off", json={}, headers=bearer(MARCUS)
            ).status_code
            == 403
        )
        client.post(f"{CLAIM}/sign-off", json={})
        client.post(f"{CLAIM}/payment", json={"amount": 1250})
    with SignedClient(app) as client:
        detail = client.get(CLAIM).json()
        assert detail["decision"]["payout_cents"] == 125000
        assert detail["sign_off"]["handler_id"] == "HND-01"
        assert detail["payment"]["amount_cents"] == 125000


def test_concurrent_decisions_replay_without_changing_business_fields(client):
    payload = decision()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: client.post(f"{CLAIM}/decision", json=payload), range(2))
        )
    assert [r.status_code for r in results] == [200, 200]
    saved = results[0].json()
    assert results[1].json() == saved == client.get(CLAIM).json()
    assert len(client.app.state.store.all("decisions")) == 1
    for change in (
        {"explanation": "A different explanation."},
        {"calculation": "A different calculation."},
        {"payout_amount": 419},
        {"policy_references": ["3"]},
        {"manual_references": ["2"]},
        {"outcome": "refer", "payout_amount": 0},
    ):
        conflicting = client.post(f"{CLAIM}/decision", json=payload | change)
        assert conflicting.status_code == 409
    equivalent = client.post(
        f"{CLAIM}/decision", json=payload | {"payout_amount": "420.00"}
    )
    assert equivalent.json() == saved


def test_payment_replay_after_response_loss_and_restart(tmp_path):
    path = tmp_path / "lost-response.sqlite3"
    with SignedClient(create_app(path)) as client:
        client.post(f"{CLAIM}/decision", json=decision(1250)).raise_for_status()
        signed = client.post(f"{CLAIM}/sign-off", json={}).json()
        client.post(f"{CLAIM}/payment", json={"amount": 1250}).raise_for_status()
        first = client.get(CLAIM).json()
    reopened = create_app(path)
    with SignedClient(reopened) as client:
        replayed = client.post(f"{CLAIM}/payment", json={"amount": "1250.00"})
        assert replayed.json() == first
        assert replayed.json()["payment"]["sign_off"] == signed
        assert len(reopened.state.store.all("payments")) == 1


def test_sign_off_checks_financial_facts_in_one_read(client):
    client.post(f"{CLAIM}/decision", json=decision(1250)).raise_for_status()
    statements = []

    def record(conn, cursor, statement, *_):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = client.app.state.store.engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        client.post(f"{CLAIM}/sign-off", json={}).raise_for_status()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(statements) == 2  # Handler lookup and the financial facts query.


def test_settlement_tracks_committed_facts_without_fetching_full_detail(client):
    endpoint = f"{CLAIM}/settlement"
    assert client.get("/api/claims/missing/settlement").status_code == 404
    client.post(f"{CLAIM}/decision", json=decision(1250)).raise_for_status()
    assert client.get(endpoint).json() == {
        "status": "waiting_sign_off",
        "sign_off": None,
        "payment": None,
    }
    signed = client.post(f"{CLAIM}/sign-off", json={}).json()
    assert client.get(endpoint).json() == {
        "status": "in_progress",
        "sign_off": signed,
        "payment": None,
    }
    paid = client.post(f"{CLAIM}/payment", json={"amount": 1250}).json()
    statements = []

    def observe(conn, cursor, statement, *_):
        statements.append(statement)

    engine = client.app.state.store.engine
    event.listen(engine, "before_cursor_execute", observe)
    try:
        summary = client.get(endpoint).json()
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert summary == {key: paid[key] for key in ("status", "sign_off", "payment")}
    assert len(statements) == 1
    assert "customer" not in statements[0].lower()
