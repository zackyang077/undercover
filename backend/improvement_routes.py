"""Handler actions, service admission and the improver's restricted backend API."""

import os
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend import configuration as config
from backend.auth import CurrentHandler, SeniorHandler
from backend.improvement import Rounds
from backend.round_evaluations import Evaluations
from backend.storage import Store

Text = Annotated[str, Field(min_length=1)]


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfigurationChange(StrictBody):
    instructions: Text
    model: Text
    reasoning_effort: Text
    reason: Text


class RecipeChange(StrictBody):
    recipe: Text
    reason: Text


class ConfigProposal(StrictBody):
    kind: Literal["configuration"]
    args: ConfigurationChange


class RecipeProposal(StrictBody):
    kind: Literal["recipe"]
    args: RecipeChange


class Review(StrictBody):
    proposal_id: Text
    choice: Literal["approve", "reject"]
    reason: str


class HandlerReview(Review):
    handler_id: Text


class Started(StrictBody):
    handler_id: Text


class Stopped(StrictBody):
    reason: Text


class Usage(StrictBody):
    call_id: Text
    cost: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None


def forward(path, body):
    address = os.getenv("UNDERCOVER_AGENT", "http://127.0.0.1:8002").rstrip("/")
    try:
        response = httpx.post(f"{address}{path}", json=body, timeout=10)
    except httpx.RequestError as exc:
        raise HTTPException(503, "Improver service is unavailable") from exc
    if response.is_error:
        raise HTTPException(response.status_code, response.json()["detail"])
    return response.json()


def routes(store: Store):
    router = APIRouter(prefix="/api")
    rounds = Rounds(store)
    evaluations = Evaluations(store)

    @router.get("/improvement", operation_id="list_improvement_rounds")
    def list_improvement_rounds(handler: CurrentHandler):
        """Read rounds, costs and recipe changes for the signed-in desk."""
        with store.connection():
            rows = sorted(
                store.all("rounds"), key=lambda row: row["started_at"], reverse=True
            )
            return {
                "rounds": [{**r, "costs": evaluations.costs(r)} for r in rows],
                "recipes": config.history(store, "recipe"),
                "current_version": config.current(store),
            }

    @router.post(
        "/improvement/rounds", status_code=202, operation_id="start_improvement_round"
    )
    def start_improvement_round(handler: SeniorHandler):
        """Start a round with the signed-in senior handler."""
        return forward("/improvement/start", {"handler_id": handler.handler_id})

    @router.post(
        "/improvement/rounds/{round_id}/review",
        status_code=202,
        operation_id="review_proposal",
    )
    def review_proposal(round_id: str, body: Review, handler: SeniorHandler):
        """Ask the service to record a review and resume the improver."""
        return forward(
            f"/improvement/{round_id}/review",
            {**body.model_dump(), "handler_id": handler.handler_id},
        )

    @router.post(
        "/improvement/rounds/{round_id}/stop", operation_id="stop_improvement_round"
    )
    def stop_improvement_round(round_id: str, handler: SeniorHandler):
        """Stop the round and record the responsible handler."""
        return rounds.stop(round_id, "Stopped by handler", handler.handler_id)

    router.include_router(execution_routes(rounds, evaluations))
    router.include_router(proposal_routes(rounds))
    return router


internal = "/improver/rounds/{round_id}"


def execution_routes(rounds, evaluations):
    router = APIRouter()
    store = rounds.store

    @router.post("/improver/rounds", operation_id="open_improver_round")
    def open_improver_round(body: Started):
        """Allocate a round after the service acquires its work lock."""
        return rounds.create(body.handler_id)

    @router.post("/improver/recover", operation_id="recover_improver_rounds")
    def recover_improver_rounds():
        """Stop interrupted work and preserve pending reviews."""
        rounds.recover()
        return {"status": "ok"}

    @router.get(internal, operation_id="read_improver_round")
    def read_improver_round(round_id: str):
        """Read durable execution state for one round."""
        return rounds.get(round_id)

    @router.get(internal + "/results", operation_id="read_improver_results")
    def read_improver_results(round_id: str):
        """Return evaluation examples and held-out aggregates to the improver."""
        with store.connection():
            return rounds.results(round_id, evaluations.remaining(rounds.get(round_id)))

    @router.post(internal + "/next-evaluation", operation_id="select_round_evaluation")
    def select_round_evaluation(round_id: str):
        """Select a missing trial or decide the candidate verdict."""
        return evaluations.next(round_id)

    @router.get(internal + "/can-work/{run_id}", operation_id="check_round_admission")
    def check_round_admission(round_id: str, run_id: str):
        """Check remaining budget before starting work on a claim."""
        return evaluations.can_work(round_id, run_id)

    @router.post(internal + "/reserve-call", operation_id="reserve_improver_call")
    def reserve_improver_call(round_id: str):
        """Reserve a bounded model call on the open round."""
        return evaluations.reserve_call(round_id)

    @router.post(internal + "/usage", operation_id="record_improver_charge")
    def record_improver_charge(round_id: str, body: Usage):
        """Record actual usage for one improver model invocation."""
        return evaluations.charge(round_id, body.call_id, body.cost)

    return router


def proposal_routes(rounds):
    router = APIRouter()

    @router.post(internal + "/proposal", operation_id="record_pending_proposal")
    def record_pending_proposal(round_id: str, body: ConfigProposal | RecipeProposal):
        """Persist one validated proposal for human review."""
        return rounds.proposal(round_id, body.kind, body.args.model_dump())

    @router.post(internal + "/review", operation_id="record_handler_review")
    def record_handler_review(round_id: str, body: HandlerReview):
        """Record a review accepted by the service before resuming."""
        return rounds.review(round_id, body.model_dump())

    @router.post(internal + "/finish", operation_id="finish_improver_round")
    def finish_improver_round(round_id: str):
        """Finish a round and preserve the recorded verdict."""
        return rounds.finish(round_id)

    @router.post(internal + "/stop", operation_id="interrupt_improver_round")
    def interrupt_improver_round(round_id: str, body: Stopped):
        """Close a round interrupted while executing."""
        return rounds.stop(round_id, body.reason, "Agent service")

    return router
