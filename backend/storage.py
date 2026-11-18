"""Portable business storage and explicit, one-time seed loading."""

import json
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from threading import local
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Connection, func, select
from sqlalchemy.schema import CreateSchema

from backend.db import attempts, engine_for, key, metadata, records, runs
from backend.guidance import initialize as initialize_guidance
from backend.schemas import Claim, ClaimDetail, ClaimSummary, Customer, Plan, Policy

DATA = Path(__file__).resolve().parents[1] / "data"
SEEDS = {
    "claims": "claim_id",
    "policies": "policy_id",
    "customers": "customer_id",
    "handlers": "handler_id",
}
MAX_ATTEMPTS = 2
COMPANY_FIELDS = ("name", "currency", "payout_approval_limit", "policy_wording_version")


def claim_status(claim_id, data, running, failures, limit) -> str:
    decision = data["decisions"].get(claim_id)
    signed = data["sign_offs"].get(claim_id)
    if decision:
        if (
            decision["outcome"] == "approve"
            and decision["payout_amount"] > limit
            and not signed
        ):
            return "waiting_sign_off"
        return "decided"
    if running:
        return "in_progress"
    return "needs_handler" if failures >= MAX_ATTEMPTS else "waiting"


class Store:
    """Own the engine and share a connection across nested synchronous operations."""

    def __init__(self, path: Path | str):
        self.path = path
        self.engine = engine_for(path)
        self._local = local()

    @contextmanager
    def connection(self, *, write: bool = False):
        current = getattr(self._local, "connection", None)
        if current is not None:
            yield current
            return
        with self.engine.begin() as connection:
            if write:
                if self.engine.dialect.name == "sqlite":
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    # Serialize the short mutations which share queue or claim state.
                    connection.execute(select(func.pg_advisory_xact_lock(170418, 1)))
            self._local.connection = connection
            try:
                yield connection
            finally:
                del self._local.connection

    def initialize(self):
        with self.connection(write=True) as connection:
            if self.engine.dialect.name == "postgresql":
                connection.execute(CreateSchema("undercover", if_not_exists=True))
            metadata.create_all(connection)
            initialize_guidance(self, connection, DATA)
            if self.find(connection, "settings", "company"):
                return
            for kind, id_field in SEEDS.items():
                source = json.loads((DATA / f"{kind}.json").read_text(encoding="utf-8"))
                connection.execute(
                    records.insert(),
                    [
                        {"kind": kind, "id": row[id_field], "body": json.dumps(row)}
                        for row in source
                    ],
                )
            plans = json.loads((DATA / "plans.json").read_text())
            for plan in plans["plans"]:
                self.insert(connection, "plans", plan["plan"], plan)
            self.insert(connection, "settings", "plans", plans)
            company = json.loads((DATA / "company.json").read_text())
            self.insert(
                connection,
                "settings",
                "company",
                {key: company[key] for key in COMPANY_FIELDS},
            )
            self.insert(
                connection,
                "settings",
                "wording",
                {
                    "version": company["policy_wording_version"],
                    "content": (DATA / "policy_wording.md").read_text(),
                },
            )

    @staticmethod
    def insert(connection: Connection, kind: str, record_id: str, body: object):
        connection.execute(
            records.insert().values(kind=kind, id=record_id, body=json.dumps(body))
        )

    @staticmethod
    def update(connection: Connection, kind: str, record_id: str, body: object):
        return connection.execute(
            records.update().where(key(kind, record_id)).values(body=json.dumps(body))
        )

    @staticmethod
    def find(
        connection: Connection, kind: str, record_id: str
    ) -> dict[str, Any] | None:
        body = connection.scalar(select(records.c.body).where(key(kind, record_id)))
        return json.loads(body) if body is not None else None

    def get(self, kind: str, record_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            value = self.find(connection, kind, record_id)
        if value is None:
            raise HTTPException(404, "Record not found")
        return value

    def all(self, kind: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.scalars(
                select(records.c.body)
                .where(records.c.kind == kind)
                .order_by(records.c.id)
            )
            return [json.loads(body) for body in rows]

    def _snapshot(self, connection: Connection, *, include_policies: bool = False):
        kinds = ("claims", "customers", "decisions", "sign_offs", "payments")
        settings = records.c.kind == "settings"
        if include_policies:
            kinds += ("plans", "policies")
            settings &= records.c.id.in_(("company", "plans"))
        else:
            settings &= records.c.id == "company"
        snapshot: dict[str, dict[str, Any]] = defaultdict(dict)
        query = (
            select(records)
            .where(records.c.kind.in_(kinds) | settings)
            .order_by(records.c.kind, records.c.id)
        )
        for kind, record_id, body in connection.execute(query):
            snapshot[kind][record_id] = json.loads(body)
        return snapshot

    def detail(self, claim_id: str) -> ClaimDetail:
        with self.connection() as connection:
            data = self._snapshot(connection, include_policies=True)
            if claim_id not in data["claims"]:
                raise HTTPException(404, "Record not found")
            claim = data["claims"][claim_id]
            policy = data["policies"][claim["policy_id"]]
            plans = data["settings"]["plans"]
            decision = data["decisions"].get(claim_id)
            signed = data["sign_offs"].get(claim_id)
            payment = data["payments"].get(claim_id)
            running = connection.scalar(
                select(runs.c.thread_id).where(
                    runs.c.claim_id == claim_id, runs.c.status == "running"
                )
            )
            limit = data["settings"]["company"]["payout_approval_limit"]
            status = claim_status(
                claim_id,
                data,
                running,
                self.failure_count(connection, claim_id),
                limit,
            )
            return ClaimDetail(
                claim=Claim.model_validate(claim),
                policy=Policy.model_validate(policy),
                customer=self.customer_history(claim, data),
                plan=Plan.model_validate(data["plans"][policy["plan"]]),
                add_ons=[
                    a for a in plans["add_ons"] if a["add_on"] in policy["add_ons"]
                ],
                deductible_applies_to=plans["deductible_applies_to"],
                approval_limit=limit,
                status=status,
                decision=decision,
                sign_off=signed,
                payment=payment,
            )

    @staticmethod
    def customer_history(claim: dict, data: dict) -> Customer:
        customer = data["customers"][claim["customer_id"]]
        history = {
            c["claim_id"]: c
            for c in customer["previous_claims"]
            if c["filed_on"] <= claim["submitted_on"]
        }
        for other in data["claims"].values():
            if (
                other["customer_id"] != claim["customer_id"]
                or other["submitted_on"] > claim["submitted_on"]
            ):
                continue
            decision = data["decisions"].get(other["claim_id"])
            payment = data["payments"].get(other["claim_id"])
            history[other["claim_id"]] = {
                "claim_id": other["claim_id"],
                "filed_on": other["submitted_on"],
                "summary": other["summary"],
                "outcome": decision["outcome"] if decision else "open",
                "amount_paid": payment["amount"] if payment else 0,
            }
        history.pop(claim["claim_id"], None)
        return Customer.model_validate(
            {
                **customer,
                "previous_claims": sorted(
                    history.values(), key=lambda c: c["filed_on"]
                ),
            }
        )

    def failures(self, claim_id: str) -> int:
        with self.connection() as connection:
            return self.failure_count(connection, claim_id)

    @staticmethod
    def failed_attempts():
        return (
            select(runs.c.claim_id, func.count().label("count"))
            .select_from(runs.join(attempts, attempts.c.thread_id == runs.c.thread_id))
            .where(runs.c.status.in_(("failed", "interrupted")))
            .group_by(runs.c.claim_id)
        )

    @staticmethod
    def failure_count(connection: Connection, claim_id: str) -> int:
        row = connection.execute(
            Store.failed_attempts().where(runs.c.claim_id == claim_id)
        ).first()
        return row[1] if row else 0

    def queue(self) -> list[ClaimSummary]:
        with self.connection() as connection:
            data = self._snapshot(connection)
            limit = data["settings"]["company"]["payout_approval_limit"]
            running = set(
                connection.scalars(
                    select(runs.c.claim_id).where(runs.c.status == "running")
                )
            )
            failures = {r[0]: r[1] for r in connection.execute(self.failed_attempts())}
        result = []
        for cid, claim in data["claims"].items():
            decision = data["decisions"].get(cid)
            payment = data["payments"].get(cid)
            outcome = decision["outcome"] if decision else ""
            result.append(
                ClaimSummary(
                    claim_id=cid,
                    customer_name=data["customers"][claim["customer_id"]]["name"],
                    summary=claim["summary"],
                    claim_type=claim["claim_type"],
                    amount_claimed=claim["amount_claimed"],
                    status=claim_status(
                        cid,
                        data,
                        cid in running,
                        failures.get(cid, 0),
                        limit,
                    ),
                    outcome=outcome,
                    payout_amount=decision["payout_cents"] / 100 if decision else None,
                    payment_status="paid"
                    if payment
                    else ("unpaid" if outcome == "approve" else "not_applicable"),
                )
            )
        return result
