from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from unittest.mock import MagicMock, Mock

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.pq import TransactionStatus

from agent import checkpoints


def claim_config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


def test_thread_survives_reopening_local_checkpoint_file():
    config = claim_config("attempt-1")
    checkpoint = empty_checkpoint()
    with checkpoints.open_saver() as saver:
        saver.put(config, checkpoint, {"step": 3}, {})
    with checkpoints.open_saver() as reopened:
        recovered = reopened.get_tuple(config)
        assert recovered
        assert recovered.checkpoint["id"] == checkpoint["id"]
        assert recovered.metadata.get("step") == 3


def postgres_connection(read_checkpoint: Callable[[], None]) -> MagicMock:
    connection = MagicMock()
    connection.pgconn.transaction_status = TransactionStatus.IDLE
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value.fetchone.side_effect = (
        read_checkpoint
    )
    return connection


def test_postgres_savers_share_pool_and_wait_for_busy_connections(monkeypatch):
    worker_count, connection_limit = 6, 4
    ready = Barrier(worker_count)
    pool_busy, release = Event(), Event()
    lock = Lock()
    active = 0
    peak = 0

    def read_checkpoint():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == connection_limit:
                pool_busy.set()
        try:
            assert release.wait(timeout=5)
        finally:
            with lock:
                active -= 1

    setup = Mock()
    monkeypatch.setattr(
        Connection,
        "connect",
        lambda *_args, **_kwargs: postgres_connection(read_checkpoint),
    )
    monkeypatch.setattr(PostgresSaver, "setup", setup)
    postgres = checkpoints.PostgresCheckpoints()
    monkeypatch.setattr(checkpoints, "postgres", postgres)
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/undercover")

    def work(index: int) -> PostgresSaver:
        with checkpoints.open_saver() as saver:
            assert isinstance(saver, PostgresSaver)
            # All six invocations open their savers before any connection checkout.
            ready.wait(timeout=5)
            assert saver.get_tuple(claim_config(f"attempt-{index}")) is None
            return saver

    try:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(work, index) for index in range(worker_count)]
            try:
                assert pool_busy.wait(timeout=5)
            finally:
                release.set()
            savers = [future.result(timeout=5) for future in futures]
        assert len({id(saver) for saver in savers}) == worker_count
        assert all(saver.conn is postgres.pool for saver in savers)
        assert postgres.pool is not None
        stats = postgres.pool.get_stats()
        assert stats["pool_max"] == connection_limit
        assert stats["pool_available"] == connection_limit
        assert peak == connection_limit
        setup.assert_called_once()
    finally:
        postgres.close()


def test_parallel_claims_keep_independent_local_checkpoint_threads():
    claim_count = 25
    ready = Barrier(claim_count)

    def write(index: int):
        config = claim_config(f"attempt-{index}")
        checkpoint = empty_checkpoint()
        with checkpoints.open_saver() as saver:
            ready.wait(timeout=5)
            saver.put(config, checkpoint, {"step": index}, {})
        return config, checkpoint["id"]

    with ThreadPoolExecutor(max_workers=claim_count) as executor:
        results = list(executor.map(write, range(claim_count)))
    with checkpoints.open_saver() as saver:
        for index, (config, checkpoint_id) in enumerate(results):
            recovered = saver.get_tuple(config)
            assert recovered is not None
            assert recovered.checkpoint["id"] == checkpoint_id
            assert recovered.metadata.get("step") == index
