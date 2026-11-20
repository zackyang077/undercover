"""Portable business storage and explicit, one-time seed loading."""

import json
from contextlib import contextmanager
from pathlib import Path
from threading import local
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Connection, func, select
from sqlalchemy.schema import CreateSchema

from backend.claim_queries import ClaimQueries
from backend.configuration import initialize as initialize_configuration
from backend.db import attempts, engine_for, key, metadata, records, runs
from backend.eval_sources import initialize as initialize_evaluations
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


class Store:
    """Own the engine and share a connection across nested synchronous operations."""

    def __init__(self, path: Path | str):
        self.path = path
        self.engine = engine_for(path)
        self._local = local()
        self.queries = ClaimQueries(
            self.engine.dialect.name == "postgresql", MAX_ATTEMPTS
        )

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
            if not self.find(connection, "settings", "company"):
                self._seed_live_records(connection)
                self.insert(connection, "migrations", "handler_sign_in", {})
            elif self.find(connection, "migrations", "handler_sign_in") is None:
                self._upgrade_handler_identities(connection)
            initialize_evaluations(connection, DATA)
            initialize_configuration(self, connection, DATA)

    def _upgrade_handler_identities(self, connection: Connection):
        handlers = json.loads((DATA / "handlers.json").read_text(encoding="utf-8"))
        for identity in handlers:
            handler_id = identity["handler_id"]
            saved = self.find(connection, "handlers", handler_id)
            if saved is not None:
                saved.update(
                    email=identity["email"],
                    can_approve_over_limit=identity["can_approve_over_limit"],
                )
                self.update(connection, "handlers", handler_id, saved)
        self.insert(connection, "migrations", "handler_sign_in", {})

    def _seed_live_records(self, connection: Connection):
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

    def detail(self, claim_id: str) -> ClaimDetail:
        with self.connection() as conn:
            row = (
                conn.execute(self.queries.detail, {"requested_claim": claim_id})
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise HTTPException(404, "Record not found")
            claim, policy = json.loads(row["claim"]), json.loads(row["policy"])
            plans = json.loads(row["plans"])
            customer = json.loads(row["customer"])
            source_id = row["evaluation_source_id"]
            history = {
                c["claim_id"]: c
                for c in customer["previous_claims"]
                if c["filed_on"] <= claim["submitted_on"]
            }
            if source_id is None:
                previous = conn.execute(
                    self.queries.history,
                    {
                        "current_claim": claim_id,
                        "customer_id": claim["customer_id"],
                        "filed_on": claim["submitted_on"],
                    },
                ).mappings()
                history.update((r["claim_id"], dict(r)) for r in previous)
        history.pop(source_id or claim_id, None)
        customer["previous_claims"] = sorted(
            history.values(), key=lambda c: c["filed_on"]
        )
        return ClaimDetail(
            claim=Claim.model_validate(claim),
            policy=Policy.model_validate(policy),
            customer=Customer.model_validate(customer),
            plan=Plan.model_validate_json(row["plan"]),
            add_ons=[a for a in plans["add_ons"] if a["add_on"] in policy["add_ons"]],
            deductible_applies_to=plans["deductible_applies_to"],
            approval_limit=row["approval_limit"],
            evaluation_copy=source_id is not None,
            evaluation_run_id=row["evaluation_run_id"],
            status=row["status"],
            decision=json.loads(row["decision"]) if row["decision"] else None,
            payment=json.loads(row["payment"]) if row["payment"] else None,
            sign_off=json.loads(row["sign_off"]) if row["sign_off"] else None,
        )

    def failures(self, claim_id: str) -> int:
        with self.connection() as connection:
            return self.failure_count(connection, claim_id)

    @staticmethod
    def failed_attempts():
        return (
            select(runs.c.claim_id, func.count().label("count"))
            .select_from(runs.join(attempts, attempts.c.thread_id == runs.c.thread_id))
            .where(runs.c.status == "failed")
            .group_by(runs.c.claim_id)
        )

    @staticmethod
    def failure_count(connection: Connection, claim_id: str) -> int:
        row = connection.execute(
            Store.failed_attempts().where(runs.c.claim_id == claim_id)
        ).first()
        return row[1] if row else 0

    def queue(self) -> list[ClaimSummary]:
        with self.connection() as conn:
            return [
                ClaimSummary.model_validate(r)
                for r in conn.execute(self.queries.queue).mappings()
            ]
