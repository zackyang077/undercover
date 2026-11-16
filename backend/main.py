"""The UnderCover backend."""

import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from pydantic import JsonValue

from agent.main import run as run_agent
from backend.finance import Finance
from backend.guidance import routes as guidance_routes
from backend.queue_runs import QueueRuns
from backend.queue_runs import routes as queue_routes
from backend.runs import Runs, routes
from backend.schemas import (
    ClaimDetail,
    ClaimSummary,
    DecisionInput,
    Handler,
    PaymentInput,
    SignOffInput,
    Wording,
)
from backend.storage import Store


def create_app(path: Path, runner: Callable[[str, str], str] = run_agent) -> FastAPI:
    """Create a backend with an isolated database location."""
    store = Store(path)
    runs = Runs(store, runner)
    queue = QueueRuns(runs)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        store.initialize()
        runs.recover()
        queue.recover()
        yield
        await queue.close()
        await runs.close()

    application = FastAPI(title="UnderCover", lifespan=lifespan)
    application.state.store = store
    application.state.runs = runs
    application.state.queue = queue

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

    @application.get("/api/agent/instructions", operation_id="get_agent_instructions")
    def get_agent_instructions() -> dict[str, str]:
        """Read the instructions used by the claims agent."""
        return {"content": Path(__file__).with_name("instructions.md").read_text()}

    application.include_router(guidance_routes(store))
    application.include_router(financial_routes(store))
    application.include_router(routes(runs))
    application.include_router(queue_routes(queue))
    return application


def financial_routes(store: Store) -> APIRouter:
    router = APIRouter()
    finance = Finance(store)

    @router.post("/api/claims/{claim_id}/decision", operation_id="record_decision")
    def record_decision(claim_id: str, decision: DecisionInput) -> dict[str, JsonValue]:
        """Save the agent's final decision."""
        return finance.decide(claim_id, decision)

    @router.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off_claim")
    def sign_off_claim(claim_id: str, request: SignOffInput) -> dict[str, JsonValue]:
        """Record handler sign-off on an approved claim."""
        return finance.sign_off(claim_id, request)

    @router.post("/api/claims/{claim_id}/payment", operation_id="pay_claim")
    def pay_claim(claim_id: str, request: PaymentInput) -> dict[str, JsonValue]:
        """Record a simulated payment after enforcing the approval gate."""
        return finance.pay(claim_id, request)

    return router


app = create_app(Path(os.getenv("UNDERCOVER_DB", "undercover.sqlite3")))
