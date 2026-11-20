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
    monkeypatch.setattr("backend.dispatch.notify", lambda path: None)


@pytest.fixture(autouse=True)
def isolated_handler_keys(monkeypatch):
    from jwt import PyJWKClient
    from backend.tests.handler_session import PROJECT, PUBLIC

    monkeypatch.setenv("VITE_SUPABASE_URL", PROJECT)
    monkeypatch.delenv("HANDLER_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(
        PyJWKClient, "fetch_data", lambda self: {"keys": [{**PUBLIC, "kid": "offline"}]}
    )
