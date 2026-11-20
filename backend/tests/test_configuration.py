from fastapi.testclient import TestClient

from backend.configuration import history
from backend.db import key, records
from backend.main import create_app
from backend.storage import Store
from backend.tests.handler_session import MARCUS, PRIYA, SignedClient, bearer
from backend.tests.test_finance import decision


def test_settings_apply_to_new_decisions_and_preserve_old_holds(tmp_path):
    path = tmp_path / "settings.db"
    app = create_app(path)
    with SignedClient(app) as client:
        first = "/api/claims/CLM-1001"
        second = "/api/claims/CLM-1002"
        body = decision(1250)
        client.post(first + "/decision", json=body).raise_for_status()
        settings = client.get("/api/settings").json()["settings"]
        assert settings["budget"] == 0.5
        changed = {**settings, "approval_limit": 1500}
        for email in (MARCUS, PRIYA):
            assert client.get("/api/settings", headers=bearer(email)).status_code == 200
            assert (
                client.put(
                    "/api/settings", json=changed, headers=bearer(email)
                ).status_code
                == 403
            )
        with TestClient(app) as anonymous:
            assert anonymous.put("/api/settings", json=changed).status_code == 401
        response = client.put("/api/settings", json=changed)
        assert response.json()["history"][0]["handler_id"] == "HND-01"
        assert (
            client.post(first + "/decision", json=body).json()["approval_limit"] == 1000
        )
        assert client.post(first + "/payment", json={"amount": 1250}).status_code == 403
        assert (
            client.post(second + "/decision", json=body).json()["approval_limit"]
            == 1500
        )
        assert (
            client.post(second + "/payment", json={"amount": 1250}).status_code == 200
        )
        assert client.post(first + "/sign-off", json={}).status_code == 200
        assert client.post(first + "/payment", json={"amount": 1250}).status_code == 200


def test_configuration_history_and_restricted_model(tmp_path, monkeypatch):
    app = create_app(tmp_path / "versions.db")
    with SignedClient(app) as client:
        store = app.state.store
        first = store.get("configuration", "1")
        with store.connection(write=True) as connection:
            store.insert(
                connection,
                "configuration",
                "2",
                {
                    **first,
                    "version": 2,
                    "instructions": "Read all evidence.",
                    "model": "candidate",
                },
            )
        result = client.get("/api/configuration").json()
        assert result["current"] == 1
        second, original = result["versions"]
        assert second["previous_model"] == first["model"]
        assert "+Read all evidence." in second["diff"]
        assert original["diff"] == []
        assert history(store, "recipe")[0]["recipe"].count("propose_recipe") == 1
        monkeypatch.setenv("MODEL_NAME", "offline/test")
        model = client.get("/api/settings").json()["models"]
        assert len(model) == 1
        assert model[0]["input_price"] == model[0]["output_price"] == 0
        config = client.get("/api/agent/configuration/current").json()
        assert set(config) == {"version", "instructions", "model", "reasoning_effort"}
        assert config["model"] == "offline/test"
        assert config["reasoning_effort"] == "default"


def test_existing_decision_captures_last_limit_on_upgrade(tmp_path):
    store = Store(tmp_path / "old.db")
    store.initialize()
    with store.connection(write=True) as connection:
        connection.execute(
            records.delete().where(records.c.kind.in_(["configuration", "recipe"]))
        )
        for name in ("improvement", "models", "versions"):
            connection.execute(records.delete().where(key("settings", name)))
        store.insert(connection, "decisions", "legacy", {"outcome": "approve"})
    store.initialize()
    assert store.get("decisions", "legacy")["approval_limit"] == 1000
