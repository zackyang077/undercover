import sqlite3
from contextlib import closing

from sqlalchemy import func, select

from backend.db import model_usage, swarm_claims, swarm_runs, work_segments
from backend.eval_sources import evaluation_copies, handler_references
from backend.finance import Finance
from backend.reseed import reseed
from backend.runs import Runs
from backend.schemas import DecisionInput
from backend.storage import Store
from backend.swarm import RunInput, Swarm
from backend.tests.test_finance import decision


def test_reseed_preserves_backup_and_restores_waiting(tmp_path):
    path = tmp_path / "trial.sqlite3"
    assert reseed(path) is None
    checkpoints = path.with_name(path.name + ".checkpoints.sqlite3")
    with closing(sqlite3.connect(checkpoints)) as connection:
        connection.execute("CREATE TABLE snapshots (thread_id TEXT)")
        connection.execute("INSERT INTO snapshots VALUES ('before-reseed')")
        connection.commit()
    store = Store(path)
    Finance(store).decide("CLM-1001", DecisionInput.model_validate(decision()))
    Swarm(Runs(store, None)).create(
        RunInput(
            run_id="before-reseed",
            kind="evaluation",
            concurrency=5,
            model="stub",
            reasoning_effort=None,
            dataset="evaluation",
            size="tiny",
        )
    )
    with store.connection(write=True) as connection:
        connection.execute(
            model_usage.insert().values(
                thread_id="thread", call_id="call", claim_id="copy"
            )
        )
        connection.execute(
            work_segments.insert().values(
                thread_id="thread",
                segment_id="segment",
                claim_id="copy",
                started_at="2026-11-19T00:00:00+00:00",
                elapsed_ms=12,
            )
        )
    backup = reseed(path)
    assert backup is not None
    assert backup.exists()
    assert not checkpoints.exists()
    (sidecar_backup,) = tmp_path.glob("*.checkpoints.sqlite3.*.bak")
    with closing(sqlite3.connect(sidecar_backup)) as connection:
        assert connection.execute("SELECT thread_id FROM snapshots").fetchone() == (
            "before-reseed",
        )
    with sqlite3.connect(backup) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM records WHERE kind='decisions'"
            ).fetchone()[0]
            == 1
        )
    assert len(store.queue()) == 25
    assert all(c.status == "waiting" for c in store.queue())
    erased = [swarm_runs, swarm_claims, evaluation_copies, model_usage, work_segments]
    with store.connection() as connection:
        assert all(
            connection.scalar(select(func.count()).select_from(table)) == 0
            for table in erased
        )
        assert (
            connection.scalar(select(func.count()).select_from(handler_references))
            == 50
        )
