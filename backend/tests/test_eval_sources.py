import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.db import engine_for, metadata, records
from backend.eval_sources import (
    EvalSet,
    EvalSize,
    create_copies,
    evaluation_copies,
    handler_references,
    held_out_sources,
    initialize,
)

TABLES = [records, held_out_sources, handler_references, evaluation_copies]
HANDLER_ONLY = "Private reference text for this synthetic example"


def write_json(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps(rows), encoding="utf-8")


@pytest.fixture
def seed_files(tmp_path):
    evals = tmp_path / "evals"
    evals.mkdir()
    for prefix, claim_file, decision_file in [
        ("Q", tmp_path / "claims.json", evals / "decisions.json"),
        ("H", evals / "held_out_claims.json", evals / "held_out_decisions.json"),
    ]:
        write_json(
            claim_file,
            [
                {
                    "claim_id": f"{prefix}-{index}",
                    "submitted_on": f"2026-11-{19 - index:02d}",
                    "summary": f"Source evidence {index}",
                }
                for index in range(1, 7)
            ],
        )
        write_json(
            decision_file,
            [
                {
                    "claim_id": f"{prefix}-{index}",
                    "decision": "approve",
                    "payout": "123.45",
                    "tiny": index <= 5,
                    "reason": HANDLER_ONLY,
                }
                for index in range(1, 7)
            ],
        )
    return tmp_path


@pytest.fixture
def connection(tmp_path):
    engine = engine_for(tmp_path / "evaluation.sqlite3")
    try:
        with engine.begin() as conn:
            metadata.create_all(conn, tables=TABLES)
            yield conn
    finally:
        engine.dispose()


def load_queue(connection, seed_files) -> list[dict]:
    queue = json.loads((seed_files / "claims.json").read_text(encoding="utf-8"))
    connection.execute(
        records.insert(),
        [
            {"kind": "claims", "id": claim["claim_id"], "body": json.dumps(claim)}
            for claim in queue
        ],
    )
    return queue


@pytest.mark.parametrize(
    "selection",
    [
        ("evaluation", "full", "Q", 6),
        ("evaluation", "tiny", "Q", 5),
        ("held_out", "full", "H", 6),
        ("held_out", "tiny", "H", 5),
    ],
)
def test_copies_are_distinct_ordered_and_hold_only_evidence(
    connection, seed_files, selection: tuple[EvalSet, EvalSize, str, int]
):
    set_name, size, prefix, count = selection
    queue = load_queue(connection, seed_files)
    initialize(connection, seed_files)

    first = create_copies(connection, "run-first", set_name, size)
    assert create_copies(connection, "run-first", set_name, size) == first
    second = create_copies(connection, "run-second", set_name, size)

    assert len(first) == len(second) == count
    assert len(set(first) | set(second)) == count * 2
    sources = {
        copy_id: source_id
        for copy_id, source_id in connection.execute(
            select(evaluation_copies.c.copy_id, evaluation_copies.c.source_id)
        )
    }
    assert [sources[copy_id] for copy_id in first] == [
        f"{prefix}-{index}" for index in range(count, 0, -1)
    ]
    stored = {
        record_id: json.loads(body)
        for record_id, body in connection.execute(select(records.c.id, records.c.body))
    }
    for copy_id in first + second:
        assert stored[copy_id]["claim_id"] == copy_id
        assert stored[copy_id]["summary"].startswith("Source evidence ")
        assert "decision" not in stored[copy_id]
        assert "payout" not in stored[copy_id]
        assert HANDLER_ONLY not in str(stored[copy_id])
    assert [stored[claim["claim_id"]] for claim in queue] == queue
    assert all(record_id.startswith(("Q-", "EVAL-")) for record_id in stored)


def test_cannot_replay_same_run_with_different_selection(connection, seed_files):
    load_queue(connection, seed_files)
    initialize(connection, seed_files)
    create_copies(connection, "run-first", "evaluation", "tiny")

    with pytest.raises(ValueError, match="different evaluation selection"):
        create_copies(connection, "run-first", "evaluation", "full")

    assert connection.scalar(select(func.count()).select_from(evaluation_copies)) == 5


def test_missing_queue_source_blocks_copy_creation(connection, seed_files):
    initialize(connection, seed_files)

    with pytest.raises(ValueError, match="Missing evaluation sources"):
        create_copies(connection, "run-first", "evaluation", "tiny")

    assert connection.scalar(select(func.count()).select_from(evaluation_copies)) == 0


@pytest.mark.parametrize(
    ("changed_file", "error"),
    [
        ("claims.json", "Duplicate claim ID"),
        ("evals/decisions.json", "Source and reference claim IDs do not match"),
        ("evals/held_out_decisions.json", "Expected 5 tiny references"),
    ],
)
def test_invalid_seed_rejected_before_writing(
    connection, seed_files, changed_file, error
):
    path = seed_files / changed_file
    rows = json.loads(path.read_text(encoding="utf-8"))
    if changed_file == "evals/held_out_decisions.json":
        rows[0]["tiny"] = False
    else:
        rows[0]["claim_id"] = rows[1]["claim_id"]
    write_json(path, rows)

    with pytest.raises(ValueError, match=error):
        initialize(connection, seed_files)

    assert connection.scalar(select(func.count()).select_from(handler_references)) == 0
    assert connection.scalar(select(func.count()).select_from(held_out_sources)) == 0


def test_course_seed_is_idempotent_and_has_full_reference_parity(connection):
    data_path = Path(__file__).resolve().parents[2] / "data"
    load_queue(connection, data_path)
    initialize(connection, data_path)
    initialize(connection, data_path)

    assert connection.scalar(select(func.count()).select_from(records)) == 25
    assert connection.scalar(select(func.count()).select_from(held_out_sources)) == 25
    for set_name in ("evaluation", "held_out"):
        where = handler_references.c.set_name == set_name
        assert connection.scalar(select(func.count()).where(where)) == 25
        assert (
            connection.scalar(
                select(func.count()).where(where, handler_references.c.tiny.is_(True))
            )
            == 5
        )
