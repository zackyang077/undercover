import json

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.schemas import DecisionInput
from backend.tests.test_finance import decision


def test_sections_search_versions_and_references(tmp_path):
    app = create_app(tmp_path / "guidance.sqlite3")
    with TestClient(app) as client:
        for document, count, version in [
            ("wording", 12, "2026.1"),
            ("manual", 14, "2026.2"),
        ]:
            index = client.get(f"/api/guidance/{document}/sections").json()
            assert len(index["sections"]) == count
            assert index["version"] == version
            assert index["introduction"]
        for query, expected in [
            ("meals receipts", "10"),
            ("wild animals", "7"),
            ("repeat claims", "9"),
        ]:
            matches = client.get(
                "/api/guidance/manual/search", params={"query": query}
            ).json()
            assert expected in {m["section_id"] for m in matches}
        assert (
            client.get("/api/guidance/manual/search", params={"query": ""}).json() == []
        )
        assert (
            client.get(
                "/api/guidance/manual/search", params={"query": "zzznomatch"}
            ).json()
            == []
        )
        section = client.get("/api/guidance/manual/sections/7").json()
        assert "Wild animals" in section["content"]
        assert "Treatment that can wait" in section["content"]
        assert "$35" in client.get("/api/guidance/manual/sections/10").json()["content"]
        assert client.get("/api/guidance/manual/sections/99").status_code == 404
        assert client.get("/api/guidance/evals/sections").status_code == 422
        assert client.get("/api/guidance/manual/search").status_code == 422
        saved = client.post("/api/claims/CLM-1001/decision", json=decision()).json()[
            "decision"
        ]
        assert saved["manual_version"] == "2026.2"
        assert saved["wording_version"] == "2026.1"
        assert saved["decided_by"] == "UnderCover Agent"
        with pytest.raises(ValueError, match="Manual references"):
            DecisionInput.model_validate({**decision(), "manual_references": ["99"]})


def test_customer_history_deduplicates_and_old_decisions_read(tmp_path):
    app = create_app(tmp_path / "history.sqlite3")
    with TestClient(app) as client:
        store = app.state.store
        first = store.get("claims", "CLM-1001")
        other = {**first, "claim_id": "SYN-OPEN", "submitted_on": "2026-01-01"}
        future = {**first, "claim_id": "SYN-FUTURE", "submitted_on": "2099-01-01"}
        with store.connection() as connection:
            store.insert(connection, "claims", other["claim_id"], other)
            store.insert(connection, "claims", future["claim_id"], future)
            legacy = {**decision(), "payout_cents": 42000}
            legacy.pop("manual_references")
            store.insert(connection, "decisions", "CLM-1001", legacy)
        detail = client.get("/api/claims/CLM-1001").json()
        ids = [c["claim_id"] for c in detail["customer"]["previous_claims"]]
        assert "SYN-OPEN" in ids
        assert "SYN-FUTURE" not in ids
        assert "CLM-1001" not in ids
        assert len(ids) == len(set(ids))
        assert "manual_version" not in detail["decision"]
        before = json.dumps(detail["decision"], sort_keys=True)
    with TestClient(app) as client:
        assert (
            json.dumps(
                client.get("/api/claims/CLM-1001").json()["decision"], sort_keys=True
            )
            == before
        )
