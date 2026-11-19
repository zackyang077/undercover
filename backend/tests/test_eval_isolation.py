from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.eval_sources import (
    create_copies,
    evaluation_copies,
    handler_references,
    held_out_sources,
)
from backend.main import create_app
from backend.storage import Store

ORIGINAL = "CLM-1001"
EXTRA_LIVE_ID = "TEST-LIVE-PRIOR"


def test_copy_detail_keeps_seed_history_and_refuses_sign_off(tmp_path, monkeypatch):
    path = tmp_path / "isolation.sqlite3"
    store = Store(path)
    store.initialize()
    original = store.get("claims", ORIGINAL)
    customer = store.get("customers", original["customer_id"])
    seed_history = store.detail(ORIGINAL).customer.previous_claims
    with store.connection(write=True) as conn:
        store.update(
            conn,
            "customers",
            original["customer_id"],
            {
                **customer,
                "previous_claims": [
                    *customer["previous_claims"],
                    {
                        "claim_id": ORIGINAL,
                        "filed_on": original["submitted_on"],
                        "summary": "Self filing is excluded",
                        "outcome": "decline",
                        "amount_paid": 0,
                    },
                ],
            },
        )
        store.insert(
            conn,
            "claims",
            EXTRA_LIVE_ID,
            {**original, "claim_id": EXTRA_LIVE_ID, "submitted_on": "2000-01-01"},
        )
        store.insert(
            conn,
            "decisions",
            EXTRA_LIVE_ID,
            {"outcome": "approve", "payout_amount": 73},
        )
        store.insert(conn, "payments", EXTRA_LIVE_ID, {"amount": 73})
        create_copies(conn, "isolation-test", "evaluation", "full")
        copy_id = conn.scalar(
            select(evaluation_copies.c.copy_id).where(
                evaluation_copies.c.source_id == ORIGINAL
            )
        )
    queued_tasks = []
    monkeypatch.setattr(
        BackgroundTasks, "add_task", lambda *args: queued_tasks.append(args)
    )

    with TestClient(create_app(path)) as client:
        original_result = client.get(f"/api/claims/{ORIGINAL}").json()
        assert not original_result["evaluation_copy"]
        live_history = {
            row["claim_id"]: row
            for row in original_result["customer"]["previous_claims"]
        }
        assert live_history[EXTRA_LIVE_ID]["amount_paid"] == 73
        assert copy_id not in live_history
        before = client.get("/api/claims").json()
        assert copy_id not in {row["claim_id"] for row in before}
        copied_url = f"/api/claims/{copy_id}"
        copied = client.get(copied_url).json()
        assert copied["evaluation_copy"]
        assert copied["claim"]["claim_id"] == copy_id
        assert copied["claim"]["summary"] == original_result["claim"]["summary"]
        assert copied["decision"] is None
        assert {field for field in copied["claim"] if field not in original} == set()
        assert copied["customer"]["previous_claims"] == [
            item.model_dump() for item in seed_history
        ]
        held = client.post(
            f"{copied_url}/decision",
            json={
                "outcome": "approve",
                "payout_amount": 1250,
                "explanation": "The stub approved this case.",
                "calculation": "Claim total is $1,250.",
                "policy_references": ["3"],
                "manual_references": ["2"],
            },
        )
        assert held.status_code == 200
        assert held.json()["status"] == "waiting_sign_off"
        signed = client.post(f"{copied_url}/sign-off", json={"handler_id": "HND-01"})
        assert signed.status_code == 403
        assert signed.json()["detail"] == "Evaluation copies cannot be signed off"
        assert (
            client.post(f"{copied_url}/payment", json={"amount": 1250}).status_code
            == 403
        )
        assert client.get(f"{copied_url}/settlement").json() == {
            "status": "waiting_sign_off",
            "sign_off": None,
            "payment": None,
        }
        assert client.get("/api/claims").json() == before
    assert not queued_tasks


def test_existing_live_seed_installs_evaluation_sources_without_reseeding(tmp_path):
    store = Store(tmp_path / "legacy.sqlite3")
    store.initialize()
    claim = {**store.get("claims", ORIGINAL), "summary": "Existing live review"}
    with store.connection(write=True) as conn:
        store.update(conn, "claims", ORIGINAL, claim)
        handler_references.drop(conn)
        held_out_sources.drop(conn)
    store.initialize()
    store.initialize()

    assert store.get("claims", ORIGINAL) == claim
    with store.connection() as conn:
        assert conn.scalar(select(func.count()).select_from(held_out_sources)) == 25
        assert conn.scalar(select(func.count()).select_from(handler_references)) == 50
