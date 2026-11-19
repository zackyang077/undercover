"""Shared SQLAlchemy tables and connection setup for both business databases."""

from pathlib import Path

from sqlalchemy import (
    URL,
    BigInteger,
    Column,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    make_url,
)
from sqlalchemy.pool import NullPool

metadata = MetaData()
records = Table(
    "records",
    metadata,
    Column("kind", Text, primary_key=True),
    Column("id", Text, primary_key=True),
    Column("body", Text, nullable=False),
)
runs = Table(
    "runs",
    metadata,
    Column("thread_id", Text, primary_key=True),
    Column("claim_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("started_at", Text, nullable=False),
    Column("finished_at", Text, nullable=False),
    Column("error", Text, nullable=False),
)
active = runs.c.status == "running"
Index(
    "one_active_run",
    runs.c.claim_id,
    unique=True,
    sqlite_where=active,
    postgresql_where=active,
)
attempts = Table(
    "harness_attempts", metadata, Column("thread_id", Text, primary_key=True)
)
events = Table(
    "events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("thread_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("tool_name", Text, nullable=False),
    Column("text", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)
Index("events_thread_order", events.c.thread_id, events.c.id)
model_usage = Table(
    "model_usage",
    metadata,
    Column("thread_id", Text, primary_key=True),
    Column("call_id", Text, primary_key=True),
    Column("claim_id", Text, nullable=False),
    Column("run_id", Text, index=True),
    Column("usage", Text),
)
work_segments = Table(
    "work_segments",
    metadata,
    Column("thread_id", Text, primary_key=True),
    Column("segment_id", Text, primary_key=True),
    Column("claim_id", Text, nullable=False),
    Column("run_id", Text, index=True),
    Column("started_at", Text, nullable=False),
    Column("finished_at", Text),
    Column("elapsed_ms", BigInteger, nullable=False),
)


def engine_for(target: Path | str):
    if isinstance(target, Path):
        return create_engine(
            URL.create("sqlite", database=str(target)),
            poolclass=NullPool,
            connect_args={"timeout": 10},
        )
    url = make_url(target)
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        raise ValueError("DATABASE_URL must be a PostgreSQL session connection")
    return create_engine(
        url.set(drivername="postgresql+psycopg"),
        pool_size=2,
        max_overflow=0,
        connect_args={"options": "-c search_path=undercover"},
    )


def key(kind: str, record_id: str):
    return (records.c.kind == kind) & (records.c.id == record_id)
