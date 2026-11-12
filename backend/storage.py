"""SQLite storage and explicit, one-time seed loading."""

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from backend.schemas import Claim, ClaimDetail, ClaimSummary, Customer, Plan, Policy

DATA = Path(__file__).resolve().parents[1] / "data"
SEEDS = {
    "claims": "claim_id",
    "policies": "policy_id",
    "customers": "customer_id",
    "handlers": "handler_id",
}
COMPANY_FIELDS = ("name", "currency", "payout_approval_limit", "policy_wording_version")


class Store:
    """Own a local database; each operation uses its own connection."""

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                yield connection

    def initialize(self):
        with self.connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS records "
                "(kind TEXT, id TEXT, body TEXT NOT NULL, PRIMARY KEY(kind, id))"
            )
            connection.executescript(
                "CREATE TABLE IF NOT EXISTS runs ("
                "thread_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL, "
                "status TEXT NOT NULL, started_at TEXT NOT NULL, "
                "finished_at TEXT NOT NULL, error TEXT NOT NULL);"
                "CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(claim_id) "
                "WHERE status='running';"
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, "
                "thread_id TEXT NOT NULL, kind TEXT NOT NULL, tool_name TEXT NOT NULL, "
                "text TEXT NOT NULL, created_at TEXT NOT NULL);"
            )
            if connection.execute("SELECT 1 FROM records LIMIT 1").fetchone():
                return
            for kind, key in SEEDS.items():
                for record in json.loads((DATA / f"{kind}.json").read_text()):
                    self.insert(connection, kind, record[key], record)
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
    def insert(connection, kind: str, record_id: str, body: object):
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?)",
            (kind, record_id, json.dumps(body)),
        )

    @staticmethod
    def find(connection, kind: str, record_id: str) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT body FROM records WHERE kind=? AND id=?", (kind, record_id)
        ).fetchone()
        return json.loads(row["body"]) if row else None

    def get(self, kind: str, record_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT body FROM records WHERE kind=? AND id=?", (kind, record_id)
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Record not found")
        return json.loads(row["body"])

    def all(self, kind: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT body FROM records WHERE kind=? ORDER BY id", (kind,)
            ).fetchall()
        return [json.loads(row["body"]) for row in rows]

    def detail(self, claim_id: str) -> ClaimDetail:
        claim = self.get("claims", claim_id)
        policy = self.get("policies", claim["policy_id"])
        plans = self.get("settings", "plans")
        with self.connection() as connection:
            decision = self.find(connection, "decisions", claim_id)
            signed = self.find(connection, "sign_offs", claim_id)
            payment = self.find(connection, "payments", claim_id)
            active = connection.execute(
                "SELECT 1 FROM runs WHERE claim_id=? AND status='running'", (claim_id,)
            ).fetchone()
        limit = self.get("settings", "company")["payout_approval_limit"]
        status = "in_progress" if active else "waiting"
        if decision:
            status = "decided"
            if (
                decision["outcome"] == "approve"
                and decision["payout_amount"] > limit
                and not signed
            ):
                status = "waiting_sign_off"
        return ClaimDetail(
            claim=Claim.model_validate(claim),
            policy=Policy.model_validate(policy),
            customer=Customer.model_validate(
                self.get("customers", claim["customer_id"])
            ),
            plan=Plan.model_validate(self.get("plans", policy["plan"])),
            add_ons=[a for a in plans["add_ons"] if a["add_on"] in policy["add_ons"]],
            deductible_applies_to=plans["deductible_applies_to"],
            approval_limit=limit,
            status=status,
            decision=decision,
            sign_off=signed,
            payment=payment,
        )

    def queue(self) -> list[ClaimSummary]:
        return [self.summary(c["claim_id"]) for c in self.all("claims")]

    def summary(self, claim_id: str) -> ClaimSummary:
        detail = self.detail(claim_id)
        outcome = str(detail.decision["outcome"]) if detail.decision else ""
        return ClaimSummary(
            claim_id=claim_id,
            customer_name=detail.customer.name,
            summary=detail.claim.summary,
            claim_type=detail.claim.claim_type,
            amount_claimed=detail.claim.amount_claimed,
            status=detail.status,
            outcome=outcome,
            payment_status="paid"
            if detail.payment
            else ("unpaid" if outcome == "approve" else "not_applicable"),
        )
