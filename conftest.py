"""Keep the entire offline suite independent of inherited developer credentials."""

import os

import pytest

for key in (
    "DATABASE_URL",
    "OPENROUTER_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
):
    os.environ.pop(key, None)


@pytest.fixture(autouse=True)
def isolated_claim_files(tmp_path, monkeypatch):
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "claims.sqlite3"))
