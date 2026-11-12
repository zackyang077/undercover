"""The UnderCover backend."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from backend.schemas import ClaimDetail, ClaimSummary, Handler, Wording
from backend.storage import Store


def create_app(path: Path) -> FastAPI:
    """Create a backend with an isolated database location."""
    store = Store(path)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        store.initialize()
        yield

    application = FastAPI(title="UnderCover", lifespan=lifespan)
    application.state.store = store

    @application.get("/api/health", operation_id="health")
    def health() -> dict[str, str]:
        """Check that the backend is running."""
        return {"status": "ok"}

    @application.get("/api/claims", operation_id="list_claims")
    def list_claims() -> list[ClaimSummary]:
        """List the claims queue."""
        return store.queue()

    @application.get("/api/claims/{claim_id}", operation_id="get_claim")
    def get_claim(claim_id: str) -> ClaimDetail:
        """Read a claim and its linked evidence."""
        return store.detail(claim_id)

    @application.get("/api/handlers", operation_id="list_handlers")
    def list_handlers() -> list[Handler]:
        """List handlers available for sign-off."""
        return [Handler.model_validate(h) for h in store.all("handlers")]

    @application.get("/api/policy-wording", operation_id="get_policy_wording")
    def get_policy_wording() -> Wording:
        """Read the complete policy wording."""
        return Wording.model_validate(store.get("settings", "wording"))

    return application


app = create_app(Path(os.getenv("UNDERCOVER_DB", "undercover.sqlite3")))
