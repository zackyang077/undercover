"""Reseed an explicitly selected local database after preserving a backup."""

import argparse
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from backend.storage import Store


def reseed(path: Path) -> Path | None:
    backup = None
    if path.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        backup = path.with_name(f"{path.name}.{stamp}.bak")
        with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
            source.backup(target)
        path.unlink()
    Store(path).initialize()
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "database", type=Path, help="Explicit local SQLite file; stop its server first"
    )
    args = parser.parse_args()
    backup = reseed(args.database)
    print(f"Seeded 25 Waiting claims in {args.database}")
    if backup:
        print(f"Previous records preserved in {backup}")


if __name__ == "__main__":
    main()
