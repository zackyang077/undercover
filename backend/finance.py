"""Decisions and the server-enforced simulated payment gate."""

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from pydantic import JsonValue

from backend.schemas import DecisionInput, PaymentInput, SignOffInput
from backend.storage import Store


def now() -> str:
    return datetime.now(UTC).isoformat()


class Finance:
    def __init__(self, store: Store):
        self.store = store

    def decide(self, claim_id: str, decision: DecisionInput) -> dict[str, JsonValue]:
        self.store.get("claims", claim_id)
        record = decision.model_dump(mode="json")
        record.update(
            payout_amount=float(decision.payout_amount),
            payout_cents=int(decision.payout_amount * 100),
            created_at=now(),
            wording_version=self.store.get("guidance", "wording")["version"],
            manual_version=self.store.get("guidance", "manual")["version"],
            decided_by="UnderCover Agent",
        )
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self.store.find(connection, "decisions", claim_id):
                raise HTTPException(409, "A final decision already exists")
            self.store.insert(connection, "decisions", claim_id, record)
        return record

    def sign_off(self, claim_id: str, request: SignOffInput) -> dict[str, JsonValue]:
        self.store.get("claims", claim_id)
        handler = self.store.get("handlers", request.handler_id)
        if not handler["can_approve_over_limit"]:
            raise HTTPException(403, "Handler cannot approve over-limit payouts")
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            decision = self.store.find(connection, "decisions", claim_id)
            if not decision or decision["outcome"] != "approve":
                raise HTTPException(409, "An approved decision is required")
            if self.store.find(connection, "sign_offs", claim_id):
                raise HTTPException(409, "Sign-off already recorded")
            record = {
                "handler_id": request.handler_id,
                "handler_name": handler["name"],
                "created_at": now(),
            }
            self.store.insert(connection, "sign_offs", claim_id, record)
        return record

    def pay(self, claim_id: str, request: PaymentInput) -> dict[str, JsonValue]:
        self.store.get("claims", claim_id)
        limit = Decimal(
            str(self.store.get("settings", "company")["payout_approval_limit"])
        )
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            decision = self.store.find(connection, "decisions", claim_id)
            if not decision or decision["outcome"] != "approve":
                raise HTTPException(409, "An approved decision is required")
            signed = self.store.find(connection, "sign_offs", claim_id)
            if decision["payout_cents"] > int(limit * 100) and not signed:
                raise HTTPException(403, "Handler sign-off is required before payment")
            if int(request.amount * 100) != decision["payout_cents"]:
                raise HTTPException(409, "Payment must equal the full approved amount")
            if self.store.find(connection, "payments", claim_id):
                raise HTTPException(409, "Payment already recorded")
            record = {
                "amount": float(request.amount),
                "amount_cents": int(request.amount * 100),
                "created_at": now(),
                "simulated": True,
            }
            self.store.insert(connection, "payments", claim_id, record)
        return record
