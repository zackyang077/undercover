import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.storage import COMPANY_FIELDS, DATA, Store


def test_records_and_restart(tmp_path, monkeypatch):
    read = Path.read_text
    opened = []

    def tracked(path, *args, **kwargs):
        opened.append(path.name)
        assert path.parent == DATA
        assert path.name in {
            "claims.json",
            "policies.json",
            "customers.json",
            "handlers.json",
            "plans.json",
            "company.json",
            "policy_wording.md",
        }
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked)
    path = tmp_path / "test.sqlite3"
    with TestClient(create_app(path)) as client:
        queue = client.get("/api/claims").json()
        assert len(queue) == 25
        assert {c["status"] for c in queue} == {"waiting"}
        assert all(c["payout_amount"] is None for c in queue)
        detail = client.get("/api/claims/CLM-1001").json()
        assert detail["customer"]["name"] == "Maya Patel"
        assert len(detail["claim"]["documents"]) == 4
        assert len(detail["claim"]["items"]) == 3
        assert detail["policy"]["policy_id"] == "POL-2001"
        assert detail["plan"]["plan"] == "premium"
        assert detail["customer"]["previous_claims"]
        assert len(client.get("/api/handlers").json()) == 3
        wording = client.get("/api/policy-wording").json()
        assert wording["version"] == "2026.1"
        assert "## 12. Definitions" in wording["content"]
        assert client.get("/api/claims/missing").status_code == 404
        schema = client.get("/openapi.json").json()
        operations = [op for path in schema["paths"].values() for op in path.values()]
        ids = [op["operationId"] for op in operations]
        assert len(set(ids)) == len(ids)
        for op in operations:
            assert all(p["required"] for p in op.get("parameters", []))
    store = Store(path)
    assert set(store.get("settings", "company")) == set(COMPANY_FIELDS)
    claim = store.get("claims", "CLM-1001")
    claim["summary"] = "Preserved"
    with store.connection() as connection:
        connection.execute(
            "UPDATE records SET body=? WHERE kind='claims' AND id='CLM-1001'",
            (json.dumps(claim),),
        )
    opened.clear()
    with TestClient(create_app(path)) as client:
        assert len(client.get("/api/claims").json()) == 25
        assert (
            client.get("/api/claims/CLM-1001").json()["claim"]["summary"] == "Preserved"
        )
    assert not opened
