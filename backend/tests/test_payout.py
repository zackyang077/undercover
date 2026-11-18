import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.tests.test_finance import decision


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "payout.sqlite3")) as client:
        yield client


def line(kind, amount, **quantities):
    return {
        "description": kind,
        "kind": kind,
        "amount": amount,
        "quantity": 1,
        "nights": 0,
        "hours": 0,
        **quantities,
    }


def quote(client, claim, benefit, lines, refund=0):
    return client.post(
        f"/api/claims/{claim}/payout-calculation",
        json={"benefit": benefit, "lines": lines, "paid_by_others": refund},
    )


def test_electronics_addon_and_deductible_only_quote(client):
    def preview():
        return quote(client, "CLM-1009", "baggage", [line("electronics", 1500)], 100)

    result = preview().json()
    assert result["payout"] == 1050
    assert result["needs_sign_off"]
    assert [s["amount"] for s in result["calculation"]] == [1200, -100, -50]
    assert preview().json() == result
    detail = client.get("/api/claims/CLM-1009").json()
    assert detail["decision"] is None
    assert detail["payment"] is None


@pytest.mark.parametrize(
    "case",
    [
        (
            "CLM-1001",
            "baggage",
            [
                line("item", 700, quantity=2),
                line("electronics", 500),
                line("cash", 80),
                line("cash", 60),
            ],
            40,
            [600, 300, 100, -40, -50],
        ),
        (
            "CLM-1015",
            "trip_delay",
            [
                line("hotel", 3000, nights=2),
                line("meals_without_receipts", 900, hours=25),
            ],
            0,
            [2100, 420, -1020],
        ),
        ("CLM-1001", "trip_cancellation", [line("expense", 7000)], 0, [7000, -3900]),
        ("CLM-1001", "trip_interruption", [line("expense", 4000)], 0, [4000, -900]),
        ("CLM-1001", "passport", [line("expense", 500)], 0, [500, -250]),
        ("CLM-1001", "baggage_delay", [line("expense", 400)], 0, [400, -100]),
        ("CLM-1001", "emergency_medical", [line("expense", 10)], 12, [10, -12, 2]),
        ("CLM-1001", "emergency_medical", [line("expense", 25)], 0, [25, -25]),
    ],
)
def test_calculation_order_and_caps(client, case):
    claim, benefit, lines, refund, steps = case
    result = quote(client, claim, benefit, lines, refund).json()
    assert [s["amount"] for s in result["calculation"]] == steps
    assert result["payout"] == sum(steps)


@pytest.mark.parametrize(("amount", "held"), [(1000.0, False), (1000.01, True)])
def test_sign_off_boundary(client, amount, held):
    response = quote(client, "CLM-1001", "trip_cancellation", [line("expense", amount)])
    assert response.json()["needs_sign_off"] is held


def test_quote_and_payment_use_the_seeded_company_limit(client):
    store = client.app.state.store
    settings = store.get("settings", "company")
    settings["payout_approval_limit"] = 160
    with store.connection() as connection:
        store.update(connection, "settings", "company", settings)
    response = quote(client, "CLM-1001", "passport", [line("expense", 160.01)])
    assert response.json()["needs_sign_off"] is True
    claim = "/api/claims/CLM-1001"
    client.post(f"{claim}/decision", json=decision(160.01)).raise_for_status()
    assert client.post(f"{claim}/payment", json={"amount": 160.01}).status_code == 403


def test_quote_rejects_unknown_claim_or_invalid_inputs(client):
    assert quote(client, "CLM-missing", "baggage", []).status_code == 404
    assert quote(client, "CLM-1001", "baggage", [line("cash", -1)]).status_code == 422
    incomplete = {"benefit": "baggage", "lines": [line("item", 29)]}
    assert (
        client.post(
            "/api/claims/CLM-1001/payout-calculation", json=incomplete
        ).status_code
        == 422
    )
