"""Backend-only evaluation sources, answer keys, and claim copies."""

import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, StrictBool, TypeAdapter
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Connection,
    Table,
    Text,
    UniqueConstraint,
    select,
)

from backend.db import metadata, records
from backend.schemas import Money

EvalSet = Literal["evaluation", "held_out"]
EvalSize = Literal["full", "tiny"]
TINY_COUNT = 5

held_out_sources = Table(
    "held_out_sources",
    metadata,
    Column("source_id", Text, primary_key=True),
    Column("body", Text, nullable=False),
)
handler_references = Table(
    "handler_references",
    metadata,
    Column("set_name", Text, primary_key=True),
    Column("source_id", Text, primary_key=True),
    Column("decision", Text, nullable=False),
    Column("payout_cents", BigInteger, nullable=False),
    Column("tiny", Boolean, nullable=False),
    CheckConstraint("set_name IN ('evaluation', 'held_out')"),
    CheckConstraint("decision IN ('approve', 'decline', 'refer')"),
    CheckConstraint(
        "(decision = 'approve' AND payout_cents > 0) OR "
        "(decision <> 'approve' AND payout_cents = 0)"
    ),
)
evaluation_copies = Table(
    "evaluation_copies",
    metadata,
    Column("copy_id", Text, primary_key=True),
    Column("run_id", Text, nullable=False),
    Column("source_id", Text, nullable=False),
    UniqueConstraint("run_id", "source_id"),
)


class _Reference(BaseModel):
    claim_id: str
    decision: Literal["approve", "decline", "refer"]
    payout: Money
    tiny: StrictBool


def _sources(path: Path):
    rows = json.loads(path.read_text(encoding="utf-8"))
    sources = {row["claim_id"]: row for row in rows}
    if len(sources) != len(rows):
        raise ValueError(f"Duplicate claim ID in {path.name}")
    return sources


def _references(path: Path, set_name: EvalSet, source_ids: set[str]):
    labels = TypeAdapter(list[_Reference]).validate_json(path.read_bytes())
    if (
        len(labels) != len(source_ids)
        or {label.claim_id for label in labels} != source_ids
    ):
        raise ValueError(f"Source and reference claim IDs do not match in {path.name}")
    if sum(label.tiny for label in labels) != TINY_COUNT:
        raise ValueError(f"Expected {TINY_COUNT} tiny references in {path.name}")
    return [
        {
            "set_name": set_name,
            "source_id": label.claim_id,
            "decision": label.decision,
            "payout_cents": int(label.payout * 100),
            "tiny": label.tiny,
        }
        for label in labels
    ]


def initialize(connection: Connection, data_path: Path) -> None:
    """Seed the static sources and answer keys in the caller's transaction."""
    if connection.scalar(select(handler_references.c.source_id).limit(1)) is not None:
        return

    evals = data_path / "evals"
    evaluation = _sources(data_path / "claims.json")
    held_out = _sources(evals / "held_out_claims.json")
    references = _references(
        evals / "decisions.json", "evaluation", set(evaluation)
    ) + _references(evals / "held_out_decisions.json", "held_out", set(held_out))
    connection.execute(
        held_out_sources.insert(),
        [
            {"source_id": source_id, "body": json.dumps(body)}
            for source_id, body in held_out.items()
        ],
    )
    connection.execute(handler_references.insert(), references)


def _selected_sources(connection: Connection, set_name: EvalSet, size: EvalSize):
    references = handler_references
    if set_name == "evaluation":
        source = records
        matching = (source.c.kind == "claims") & (source.c.id == references.c.source_id)
    else:
        source = held_out_sources
        matching = source.c.source_id == references.c.source_id
    query = (
        select(references.c.source_id, source.c.body)
        .select_from(references.outerjoin(source, matching))
        .where(references.c.set_name == set_name)
    )
    if size == "tiny":
        query = query.where(references.c.tiny.is_(True))
    selected = connection.execute(query).all()
    if not selected or any(body is None for _, body in selected):
        raise ValueError(f"Missing evaluation sources for {set_name}")
    return sorted(
        ((source_id, json.loads(body)) for source_id, body in selected),
        key=lambda row: (row[1]["submitted_on"], row[0]),
    )


def create_copies(
    connection: Connection, run_id: str, set_name: EvalSet, size: EvalSize
) -> list[str]:
    """Create or replay one run's isolated copies in submission order."""
    selected = _selected_sources(connection, set_name, size)
    existing = {
        source_id: copy_id
        for source_id, copy_id in connection.execute(
            select(evaluation_copies.c.source_id, evaluation_copies.c.copy_id).where(
                evaluation_copies.c.run_id == run_id
            )
        )
    }
    if existing:
        if existing.keys() != {source_id for source_id, _ in selected}:
            raise ValueError("Run already has a different evaluation selection")
        return [existing[source_id] for source_id, _ in selected]

    claims = []
    manifest = []
    for source_id, body in selected:
        copy_id = f"EVAL-{uuid4().hex}"
        claims.append(
            {
                "kind": "claims",
                "id": copy_id,
                "body": json.dumps({**body, "claim_id": copy_id}),
            }
        )
        manifest.append({"run_id": run_id, "source_id": source_id, "copy_id": copy_id})
    connection.execute(records.insert(), claims)
    connection.execute(evaluation_copies.insert(), manifest)
    return [entry["copy_id"] for entry in manifest]
