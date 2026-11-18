"""Reseed a stopped local database or the configured UnderCover Supabase schema."""

import argparse
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import inspect, table

from backend.db import metadata
from backend.storage import Store


def backup_local(path: Path, stamp: str) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.{stamp}.bak")
    with (
        closing(sqlite3.connect(path)) as source,
        closing(sqlite3.connect(backup)) as target,
    ):
        source.backup(target)
    path.unlink()
    return backup


def reseed(path: Path) -> Path | None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    backup = backup_local(path, stamp)
    backup_local(path.with_name(path.name + ".checkpoints.sqlite3"), stamp)
    Store(path).initialize()
    return backup


def reseed_supabase(url: str):
    store = Store(url)
    try:
        with store.connection(write=True) as connection:
            store.initialize()
            existing = set(inspect(connection).get_table_names(schema="undercover"))
            for name in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                if name in existing:
                    connection.execute(table(name, schema="undercover").delete())
            for business_table in reversed(metadata.sorted_tables):
                connection.execute(business_table.delete())
            store.initialize()
    finally:
        store.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "database", nargs="?", type=Path, help="Explicit local SQLite file"
    )
    target.add_argument(
        "--supabase", action="store_true", help="Reset UnderCover in DATABASE_URL"
    )
    args = parser.parse_args()
    if args.supabase:
        if not os.environ.get("DATABASE_URL"):
            parser.error("--supabase requires DATABASE_URL")
        reseed_supabase(os.environ["DATABASE_URL"])
        print("Seeded 25 Waiting claims in the UnderCover Supabase schema")
    else:
        backup = reseed(args.database)
        print(f"Seeded 25 Waiting claims in {args.database}")
        if backup:
            print(f"Previous records preserved in {backup}")


if __name__ == "__main__":
    main()
