from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.storage import COMPANY_FIELDS, DATA, Store


def observe_reads(monkeypatch, opened):
    read_text = Path.read_text
    read_bytes = Path.read_bytes

    def track(path):
        if path.parent == DATA.parent / "backend":
            assert path.name in {"recipe.md", "instructions.md"}
            return
        relative = path.relative_to(DATA).as_posix()
        assert relative in {
            "claims.json",
            "policies.json",
            "customers.json",
            "handlers.json",
            "improvement.json",
            "plans.json",
            "company.json",
            "policy_wording.md",
            "claims_manual.md",
            "evals/held_out_claims.json",
            "evals/decisions.json",
            "evals/held_out_decisions.json",
        }
        opened.append(relative)

    def tracked_text(path, *args, **kwargs):
        track(path)
        return read_text(path, *args, **kwargs)

    def tracked_bytes(path, *args, **kwargs):
        track(path)
        return read_bytes(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked_text)
    monkeypatch.setattr(Path, "read_bytes", tracked_bytes)


def test_records_and_restart(tmp_path, monkeypatch):
    opened = []
    observe_reads(monkeypatch, opened)
    path = tmp_path / "test.sqlite3"
    with TestClient(create_app(path)) as client:
        queue = client.get("/api/claims").json()
        assert len(queue) == 25
        assert {c["status"] for c in queue} == {"waiting"}
        assert all(c["payout_amount"] is None for c in queue)
        detail = client.get("/api/claims/CLM-1001").json()
        assert detail["evaluation_copy"] is False
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
    assert {path for path in opened if path.startswith("evals/")} == {
        "evals/held_out_claims.json",
        "evals/decisions.json",
        "evals/held_out_decisions.json",
    }
    store = Store(path)
    assert set(store.get("settings", "company")) == set(COMPANY_FIELDS)
    claim = store.get("claims", "CLM-1001")
    claim["summary"] = "Preserved"
    with store.connection() as connection:
        store.update(connection, "claims", "CLM-1001", claim)
    opened.clear()
    with TestClient(create_app(path)) as client:
        assert len(client.get("/api/claims").json()) == 25
        assert (
            client.get("/api/claims/CLM-1001").json()["claim"]["summary"] == "Preserved"
        )
    assert not opened
