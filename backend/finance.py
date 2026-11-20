"""Decisions and the server-enforced simulated payment gate."""

import json
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from pydantic import JsonValue
from sqlalchemy import case, select, tuple_

from backend.db import records, runs
from backend.eval_sources import evaluation_copies
from backend.schemas import (
    ClaimDetail,
    DecisionInput,
    PaymentInput,
    Settlement,
)
from backend.storage import Store


def now() -> str:
    return datetime.now(UTC).isoformat()


class Finance:
    def __init__(self, store: Store):
        self.store = store

    def settlement(self, claim_id: str) -> Settlement:
        with self.store.connection() as connection:
            row = (
                connection.execute(
                    self.store.queries.settlement, {"requested_claim": claim_id}
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise HTTPException(404, "Record not found")
        return Settlement.model_validate(row)

    @staticmethod
    def payment_facts(connection, claim_id: str, handler_id: str | None = None) -> dict:
        keys = [
            (kind, claim_id)
            for kind in ("claims", "decisions", "sign_offs", "payments")
        ]
        keys.append(("settings", "company"))
        claim_facts = "{}"
        if handler_id is not None:
            keys.append(("handlers", handler_id))
            is_copy = (
                select(evaluation_copies.c.copy_id)
                .where(evaluation_copies.c.copy_id == claim_id)
                .exists()
            )
            claim_facts = case((is_copy, '{"evaluation_copy":true}'), else_="{}")
        facts = {
            kind: json.loads(body)
            for kind, body in connection.execute(
                select(
                    records.c.kind,
                    case(
                        (records.c.kind == "claims", claim_facts),
                        else_=records.c.body,
                    ),
                ).where(tuple_(records.c.kind, records.c.id).in_(keys))
            )
        }
        if "claims" not in facts or (
            handler_id is not None and "handlers" not in facts
        ):
            raise HTTPException(404, "Record not found")
        return facts

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

    def sign_off(self, claim_id: str, handler_id: str) -> dict[str, JsonValue]:
        with self.store.connection(write=True) as connection:
            facts = self.payment_facts(connection, claim_id, handler_id)
            if facts["claims"].get("evaluation_copy"):
                raise HTTPException(403, "Evaluation copies cannot be signed off")
            handler = facts["handlers"]
            if not handler["can_approve_over_limit"]:
                raise HTTPException(403, "Handler cannot approve over-limit payouts")
            decision = facts.get("decisions")
            if not decision or decision["outcome"] != "approve":
                raise HTTPException(409, "An approved decision is required")
            if "sign_offs" in facts:
                raise HTTPException(409, "Sign-off already recorded")
            if "payments" in facts or decision["payout_cents"] <= self.limit_cents(
                facts
            ):
                raise HTTPException(409, "This payout does not need sign-off")
            record = {
                "handler_id": handler_id,
                "handler_name": handler["name"],
                "created_at": now(),
            }
            self.store.insert(connection, "sign_offs", claim_id, record)
            latest_thread = (
                select(runs.c.thread_id)
                .where(runs.c.claim_id == claim_id)
                .order_by(runs.c.started_at.desc())
                .limit(1)
                .scalar_subquery()
            )
            connection.execute(
                runs.update()
                .where(runs.c.thread_id == latest_thread, runs.c.status == "paused")
                .values(status="running", finished_at="", error="")
            )
        return record

    @staticmethod
    def limit_cents(facts: dict) -> int:
        limit = facts["settings"]["payout_approval_limit"]
        return int(Decimal(str(limit)) * 100)

    def pay(self, claim_id: str, request: PaymentInput) -> ClaimDetail:
        cents = int(request.amount * 100)
        with self.store.connection(write=True) as connection:
            facts = self.payment_facts(connection, claim_id)
            decision = facts.get("decisions")
            if not decision or decision["outcome"] != "approve":
                raise HTTPException(409, "An approved decision is required")
            saved = facts.get("payments")
            signed = facts.get("sign_offs")
            if (
                not saved
                and not signed
                and decision["payout_cents"] > self.limit_cents(facts)
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
