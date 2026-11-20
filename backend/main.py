"""The UnderCover backend."""

import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, FastAPI
from pydantic import JsonValue

from backend import configuration, dispatch
from backend.auth import CurrentHandler, HandlerAuth, SeniorHandler
from backend.configuration_routes import routes as configuration_routes
from backend.finance import Finance
from backend.guidance import routes as guidance_routes
from backend.improvement_routes import routes as improvement_routes
from backend.payout import routes as payout_routes
from backend.runs import Runs, routes
from backend.schemas import (
    ClaimDetail,
    ClaimSummary,
    DecisionInput,
    Handler,
    PaymentInput,
    Settlement,
    SignOffInput,
    Wording,
)
from backend.scoreboard import routes as scoreboard_routes
from backend.storage import Store
from backend.swarm import Swarm
from backend.swarm import routes as swarm_routes
from backend.work import routes as work_routes


def create_app(
    path: Path | str,
    runner: Callable[[str, str], str] | None = None,
) -> FastAPI:
    """Create a backend with an isolated database location."""
    store = Store(path)
    runs = Runs(store, runner)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        store.initialize()
        if runner is not None:
            runs.recover()
        yield
        await runs.close()
        store.engine.dispose()

    application = FastAPI(title="UnderCover", lifespan=lifespan)
    application.state.store = store
    application.state.runs = runs
    application.state.auth = HandlerAuth()

    @application.get("/api/me", operation_id="get_signed_in_handler")
    def me(handler: CurrentHandler) -> Handler:
        """Read the signed-in handler and their authority."""
        return handler

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
        claim = configuration.effective_config(store, configuration.current(store))[
            "instructions"
        ]
        return {"content": claim, "claim": claim}

    application.include_router(guidance_routes(store))
    application.include_router(configuration_routes(store))
    application.include_router(improvement_routes(store))
    application.include_router(payout_routes(store))
    application.include_router(financial_routes(store))
    application.include_router(work_routes(runs))
    application.include_router(routes(runs))
    application.include_router(swarm_routes(Swarm(runs)))
    application.include_router(scoreboard_routes(store))
    return application


def financial_routes(store: Store) -> APIRouter:
    router = APIRouter()
    finance = Finance(store)

    @router.get("/api/claims/{claim_id}/settlement", operation_id="get_settlement")
    def get_settlement(claim_id: str) -> Settlement:
        """Read the claim's current status, recorded sign-off and simulated payment."""
        return finance.settlement(claim_id)

    @router.post("/api/claims/{claim_id}/decision", operation_id="record_decision")
    def record_decision(claim_id: str, decision: DecisionInput) -> ClaimDetail:
        """Save the agent's final decision."""
        return finance.decide(claim_id, decision)

    @router.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off_claim")
    def sign_off_claim(
        claim_id: str,
        request: SignOffInput,
        background: BackgroundTasks,
        handler: SeniorHandler,
    ) -> dict[str, JsonValue]:
        """Record handler sign-off on an approved claim."""
        saved = finance.sign_off(claim_id, handler.handler_id)
        background.add_task(dispatch.notify, f"/claims/{claim_id}/resume")
        return saved

    @router.post("/api/claims/{claim_id}/payment", operation_id="pay_claim")
    def pay_claim(claim_id: str, request: PaymentInput) -> ClaimDetail:
        """Record a simulated payment after enforcing the approval gate."""
        return finance.pay(claim_id, request)

    return router


app = create_app(
    os.getenv("DATABASE_URL") or Path(os.getenv("UNDERCOVER_DB", "undercover.sqlite3"))
)
