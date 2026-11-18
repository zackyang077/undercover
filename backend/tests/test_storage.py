import json
import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import event

from backend.db import engine_for
from backend.runs import Runs
from backend.storage import Store


def test_read_queue_batches_claims_and_reuses_current_transaction(tmp_path):
    store = Store(tmp_path / "batch.sqlite3")
    store.initialize()
    statements = []

    @event.listens_for(store.engine, "before_cursor_execute")
    def capture(conn, cursor, statement, *args):
        statements.append(statement)

    with store.connection(write=True) as transaction:
        assert (
            store.update(
                transaction, "settings", "company", {"payout_approval_limit": 200}
            ).rowcount
            == 1
        )
        count_before = len(statements)
        assert len(store.queue()) == 25
        assert len(statements) - count_before == 1
        count_before = len(statements)
        assert store.detail("CLM-1001").approval_limit == 200
        assert len(statements) - count_before <= 2


def test_legacy_sqlite_layout_survives_initialization(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(path)) as database:
        database.executescript(
            "CREATE TABLE records(kind TEXT, id TEXT, body TEXT NOT NULL,"
            "PRIMARY KEY(kind,id));"
            "CREATE TABLE runs(thread_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL,"
            "status TEXT NOT NULL, started_at TEXT NOT NULL,"
            "finished_at TEXT NOT NULL, error TEXT NOT NULL);"
            "CREATE TABLE harness_attempts(thread_id TEXT PRIMARY KEY);"
            "CREATE TABLE events(id INTEGER PRIMARY KEY, thread_id TEXT NOT NULL,"
            "kind TEXT NOT NULL,"
            "tool_name TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL);"
            "INSERT INTO runs VALUES ('old','CLM-1001','completed','start','end','');"
            "INSERT INTO harness_attempts VALUES ('old');"
            "INSERT INTO events VALUES (1,'old','completed','','saved','end');"
        )
        for kind, value in [
            (
                "decisions",
                {"outcome": "approve", "payout_amount": 1200, "payout_cents": 120000},
            ),
            ("sign_offs", {"handler_id": "HND-01", "created_at": "signed"}),
            ("payments", {"amount": 1200, "created_at": "paid"}),
        ]:
            database.execute(
                "INSERT INTO records VALUES (?, 'CLM-1001', ?)",
                (kind, json.dumps(value)),
            )
        database.execute(
            "INSERT INTO records VALUES ('queue_runs', 'current', ?)",
            (json.dumps({"queue_run_id": "legacy"}),),
        )
        database.commit()
    store = Store(path)
    store.initialize()
    detail = store.detail("CLM-1001")
    assert detail.status == "decided"
    assert detail.payment == {"amount": 1200, "created_at": "paid"}
    assert detail.sign_off == {"handler_id": "HND-01", "created_at": "signed"}
    (old,) = Runs(store, lambda *_: "").list("CLM-1001")
    assert old.thread_id == "old"
    assert old.events[0].text == "saved"
    assert store.get("queue_runs", "current")["queue_run_id"] == "legacy"


def test_backend_selects_only_postgres_for_a_url():
    engine = engine_for("postgresql://postgres@pooler.invalid:5432/postgres")
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
    finally:
        engine.dispose()
    with pytest.raises(ValueError, match="PostgreSQL"):
        engine_for("sqlite:///local.db")
