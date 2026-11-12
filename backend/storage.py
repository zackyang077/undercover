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
        return ClaimDetail(
            claim=Claim.model_validate(claim),
            policy=Policy.model_validate(policy),
            customer=Customer.model_validate(
                self.get("customers", claim["customer_id"])
            ),
            plan=Plan.model_validate(self.get("plans", policy["plan"])),
            add_ons=[a for a in plans["add_ons"] if a["add_on"] in policy["add_ons"]],
            deductible_applies_to=plans["deductible_applies_to"],
            approval_limit=self.get("settings", "company")["payout_approval_limit"],
            status="waiting",
        )

    def queue(self) -> list[ClaimSummary]:
        return [
            ClaimSummary(
                claim_id=c["claim_id"],
                customer_name=self.get("customers", c["customer_id"])["name"],
                summary=c["summary"],
                claim_type=c["claim_type"],
                amount_claimed=c["amount_claimed"],
                status="waiting",
                outcome="",
                payment_status="not_applicable",
            )
            for c in self.all("claims")
        ]
