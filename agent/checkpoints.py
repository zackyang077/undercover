"""Synchronous claim checkpoints, separate from backend business writes."""

import atexit
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool


class PostgresCheckpoints:
    def __init__(self):
        self.lock = Lock()
        self.pool: ConnectionPool[Connection[DictRow]] | None = None

    def open(self, url: str) -> PostgresSaver:
        with self.lock:
            if self.pool is not None:
                return PostgresSaver(self.pool)
            pool = ConnectionPool[Connection[DictRow]](
                url,
                min_size=0,
                max_size=4,
                open=True,
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                    "options": "-c search_path=undercover",
                },
            )
            try:
                saver = PostgresSaver(pool)
                saver.setup()
            except Exception:
                pool.close()
                raise
            self.pool = pool
            return saver

    def close(self):
        with self.lock:
            if self.pool is not None:
                self.pool.close()
                self.pool = None


postgres = PostgresCheckpoints()
sqlite_setup_lock = Lock()
atexit.register(postgres.close)


def sqlite_path(database: Path) -> Path:
    return database.with_name(database.name + ".checkpoints.sqlite3")


@contextmanager
def open_saver() -> Iterator[BaseCheckpointSaver]:
    """Open the claim checkpoint store for this configured service."""
    if url := os.getenv("DATABASE_URL"):
        yield postgres.open(url)
    else:
        path = sqlite_path(Path(os.getenv("UNDERCOVER_DB", "undercover.sqlite3")))
        with SqliteSaver.from_conn_string(str(path)) as saver:
            # Cold starts must not race to set WAL mode on the same file.
            with sqlite_setup_lock:
                saver.setup()
            yield saver


def clear_threads(thread_ids: Sequence[str]) -> None:
    """Clear the checkpoints for evaluation attempts the backend has settled."""
    if thread_ids:
        with open_saver() as saver:
            for thread_id in thread_ids:
                saver.delete_thread(thread_id)
