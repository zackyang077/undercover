"""Decisions and the server-enforced simulated payment gate."""

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from pydantic import JsonValue

from backend.schemas import ClaimDetail, DecisionInput, PaymentInput, SignOffInput
from backend.storage import Store


def now() -> str:
    return datetime.now(UTC).isoformat()


class Finance:
    def __init__(self, store: Store):
        self.store = store

    def decide(self, claim_id: str, decision: DecisionInput) -> ClaimDetail:
        # Compare only the submitted business fields across a checkpoint replay.
        fields = decision.model_dump(mode="json", exclude={"payout_amount"})
        fields["payout_cents"] = int(decision.payout_amount * 100)
        with self.store.connection(write=True) as connection:
            self.store.get("claims", claim_id)
            saved = self.store.find(connection, "decisions", claim_id)
            if saved is None:
                self.store.insert(
                    connection,
                    "decisions",
                    claim_id,
                    {
                        **fields,
                        "payout_amount": float(decision.payout_amount),
                        "created_at": now(),
                        "wording_version": self.store.get("guidance", "wording")[
                            "version"
                        ],
                        "manual_version": self.store.get("guidance", "manual")[
                            "version"
                        ],
                        "decided_by": "UnderCover Agent",
                    },
                )
            elif any(saved.get(key) != value for key, value in fields.items()):
                raise HTTPException(409, "A different final decision already exists")
        return self.store.detail(claim_id)

    def sign_off(self, claim_id: str, request: SignOffInput) -> dict[str, JsonValue]:
        with self.store.connection(write=True) as connection:
            self.store.get("claims", claim_id)
            handler = self.store.get("handlers", request.handler_id)
            if not handler["can_approve_over_limit"]:
                raise HTTPException(403, "Handler cannot approve over-limit payouts")
            decision = self.store.find(connection, "decisions", claim_id)
            if not decision or decision["outcome"] != "approve":
                raise HTTPException(409, "An approved decision is required")
            if self.store.find(connection, "sign_offs", claim_id):
                raise HTTPException(409, "Sign-off already recorded")
            if (
                self.store.find(connection, "payments", claim_id)
                or decision["payout_cents"] <= self.limit_cents()
            ):
                raise HTTPException(409, "This payout does not need sign-off")
            record = {
                "handler_id": request.handler_id,
                "handler_name": handler["name"],
                "created_at": now(),
            }
            self.store.insert(connection, "sign_offs", claim_id, record)
        return record

    def limit_cents(self) -> int:
        limit = self.store.get("settings", "company")["payout_approval_limit"]
        return int(Decimal(str(limit)) * 100)

    def pay(self, claim_id: str, request: PaymentInput) -> ClaimDetail:
        cents = int(request.amount * 100)
        with self.store.connection(write=True) as connection:
            self.store.get("claims", claim_id)
            decision = self.store.find(connection, "decisions", claim_id)
            if not decision or decision["outcome"] != "approve":
                raise HTTPException(409, "An approved decision is required")
            saved = self.store.find(connection, "payments", claim_id)
            signed = self.store.find(connection, "sign_offs", claim_id)
            if (
                not saved
                and not signed
                and decision["payout_cents"] > self.limit_cents()
            ):
                raise HTTPException(403, "Handler sign-off is required before payment")
            if cents != decision["payout_cents"]:
                raise HTTPException(409, "Payment must equal the full approved amount")
            if saved is None:
                record = {
                    "amount": float(request.amount),
                    "amount_cents": cents,
                    "created_at": now(),
                    "simulated": True,
                    "sign_off": signed,
                }
                self.store.insert(connection, "payments", claim_id, record)
        return self.store.detail(claim_id)
