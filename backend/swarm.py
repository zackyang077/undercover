"""Persist dispatch membership and admit durable claim work."""

import json
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, true

from backend.agent_service import AcceptedRun, start
from backend.auth import require_handler
from backend.db import events, records, swarm_claims, swarm_runs
from backend.db import runs as attempts
from backend.db import swarm_active as active
from backend.eval_sources import EvalSet, EvalSize, create_copies
from backend.finance import now
from backend.runs import Runs
from backend.storage import MAX_ATTEMPTS
from backend.swarm_metrics import measure
from backend.telemetry import Telemetry

Concurrency = Annotated[int, Field(strict=True, ge=1, le=25)]


class StartDispatch(BaseModel):
    concurrency: Concurrency


class StartEvaluation(StartDispatch):
    dataset: EvalSet
    size: EvalSize


class RunInput(StartDispatch):
    run_id: str
    kind: Literal["dispatch", "evaluation"]
    model: str
    reasoning_effort: str | None
    dataset: EvalSet | None
    size: EvalSize | None


class ClaimFinish(BaseModel):
    error: str


class RunFinish(BaseModel):
    status: Literal["completed", "failed", "stopped"]
    error: str
    dispositions: dict[str, str]


class DispatchItem(BaseModel):
    claim_id: str
    status: str
    outcome: str | None
    payout_amount: float | None


class HeldPayout(BaseModel):
    claim_id: str
    payout_amount: float


class DispatchSummary(BaseModel):
    approved: int
    declined: int
    referred: int
    waiting_sign_off: list[HeldPayout]
    needs_handler: list[str]
    in_progress: list[str]


class DispatchRun(BaseModel):
    config_version: int | None = None
    run_id: str
    status: str
    concurrency: int
    claims: list[DispatchItem]
    summary: DispatchSummary | None
    model_cost: str | None
    model_cost_complete: bool
    elapsed_ms: int | None
    error: str


class Swarm:
    def __init__(self, runs: Runs):
        self.runs = runs
        self.store = runs.store

    def candidates(self, connection) -> list[str]:
        queue = self.store.queries.queue.order_by(None).subquery()
        statement = (
            select(queue.c.claim_id, records.c.body)
            .join(
                records,
                (records.c.id == queue.c.claim_id) & (records.c.kind == "claims"),
            )
            .where(
                queue.c.status.in_(("waiting", "in_progress"))
                | ((queue.c.status == "decided") & (queue.c.payment_status == "unpaid"))
            )
        )
        ordered = sorted(
            (json.loads(body)["submitted_on"], claim_id)
            for claim_id, body in connection.execute(statement)
        )
        return [claim_id for _, claim_id in ordered]

    def create(self, request: RunInput, version: int | None = None):
        with self.store.connection(write=True) as connection:
            if connection.scalar(
                select(swarm_runs.c.run_id).where(swarm_runs.c.run_id == request.run_id)
            ):
                return
            if version is not None:
                self.store.get("configuration", str(version))
                self.store.insert(
                    connection,
                    "run_configurations",
                    request.run_id,
                    {"version": version},
                )
            if connection.scalar(select(swarm_runs.c.run_id).where(active)):
                raise HTTPException(409, "A dispatch or evaluation is already running")
            if request.kind == "evaluation":
                if not request.dataset or not request.size:
                    raise HTTPException(422, "An evaluation requires a set and size")
                claim_ids = create_copies(
                    connection, request.run_id, request.dataset, request.size
                )
            else:
                if request.dataset or request.size:
                    raise HTTPException(422, "A dispatch works the live claims queue")
                claim_ids = self.candidates(connection)
            connection.execute(
                swarm_runs.insert().values(
                    **request.model_dump(), status="running", created_at=now(), error=""
                )
            )
            if claim_ids:
                connection.execute(
                    swarm_claims.insert(),
                    [
                        {
                            "run_id": request.run_id,
                            "claim_id": claim_id,
                            "position": position,
                            "status": "waiting",
                        }
                        for position, claim_id in enumerate(claim_ids)
                    ],
                )

    def claim_ids(self, run_id: str) -> list[str]:
        with self.store.connection() as connection:
            return list(
                connection.scalars(
                    select(swarm_claims.c.claim_id)
                    .where(swarm_claims.c.run_id == run_id)
                    .order_by(swarm_claims.c.position)
                )
            )

    def activate(self, run_id: str, claim_id: str) -> dict[str, str]:
        member = (swarm_claims.c.run_id == run_id) & (
            swarm_claims.c.claim_id == claim_id
        )
        claim = self.store.queries.claims_projection.subquery()
        latest = (
            select(attempts.c.thread_id, attempts.c.status)
            .where(attempts.c.claim_id == claim_id)
            .order_by(attempts.c.started_at.desc(), attempts.c.thread_id)
            .limit(1)
            .subquery()
        )
        with self.store.connection(write=True) as connection:
            row = connection.execute(
                select(
                    claim.c.status,
                    claim.c.outcome,
                    claim.c.payment_status,
                    latest.c.thread_id,
                    latest.c.status.label("attempt_status"),
                )
                .select_from(swarm_claims)
                .join(swarm_runs, swarm_runs.c.run_id == swarm_claims.c.run_id)
                .join(claim, claim.c.claim_id == swarm_claims.c.claim_id)
                .outerjoin(latest, true())
                .where(
                    member,
                    active,
                    swarm_claims.c.status.in_(("waiting", "in_progress")),
                )
            ).one_or_none()
            if (
                not row
                or row.status in {"waiting_sign_off", "needs_handler"}
                or (row.outcome and row.payment_status != "unpaid")
            ):
                return {}
            if row.thread_id and (
                row.outcome or row.attempt_status in {"running", "interrupted"}
            ):
                thread = row.thread_id
                if row.attempt_status != "running":
                    connection.execute(
                        attempts.update()
                        .where(attempts.c.thread_id == thread)
                        .values(status="running", finished_at="", error="")
                    )
            elif row.outcome:
                thread = self.start_payment(connection, claim_id)
            else:
                thread = self.runs.begin(connection, claim_id)
            connection.execute(
                swarm_claims.update()
                .where(member)
                .values(thread_id=thread, status="in_progress")
            )
            return {"thread_id": thread}

    @staticmethod
    def start_payment(connection, claim_id: str) -> str:
        thread = str(uuid4())
        connection.execute(
            attempts.insert().values(
                thread_id=thread,
                claim_id=claim_id,
                status="running",
                started_at=now(),
                finished_at="",
                error="",
            )
        )
        connection.execute(
            events.insert().values(
                thread_id=thread,
                kind="resumed",
                tool_name="",
                text="Continuing a saved decision",
                created_at=now(),
            )
        )
        return thread

    def finish(self, run_id: str, claim_id: str, thread_id: str, error: str):
        member = (
            (swarm_claims.c.run_id == run_id)
            & (swarm_claims.c.claim_id == claim_id)
            & (swarm_claims.c.thread_id == thread_id)
        )
        with self.store.connection(write=True) as connection:
            kind = connection.scalar(
                select(swarm_runs.c.kind)
                .join(swarm_claims, swarm_runs.c.run_id == swarm_claims.c.run_id)
                .where(member)
            )
            if not kind:
                raise HTTPException(404, "Dispatch claim attempt not found")
            result = self.runs.conclude(claim_id, thread_id, error)
            retry = result.status == "failed" and (
                self.store.failure_count(connection, claim_id) < MAX_ATTEMPTS
            )
            disposition = {
                "completed": "decided",
                "paused": "waiting_sign_off",
                "failed": "waiting" if retry else "needs_handler",
            }.get(result.status, "in_progress")
            held_copy = kind == "evaluation" and disposition == "waiting_sign_off"
            if held_copy:
                disposition = "decided"
            connection.execute(
                swarm_claims.update().where(member).values(status=disposition)
            )
        answer = {"retry": retry, "status": disposition}
        if held_copy:
            answer["clear_checkpoint"] = True
        return answer

    def close(self, run_id: str, request: RunFinish):
        with self.store.connection(write=True) as connection:
            kind = connection.scalar(
                select(swarm_runs.c.kind).where(swarm_runs.c.run_id == run_id, active)
            )
            if not kind:
                return []
            for claim_id, status in request.dispositions.items():
                if status in {"failed", "skipped"}:
                    connection.execute(
                        swarm_claims.update()
                        .where(
                            swarm_claims.c.run_id == run_id,
                            swarm_claims.c.claim_id == claim_id,
                            swarm_claims.c.status == "waiting",
                        )
                        .values(status=status)
                    )
            connection.execute(
                swarm_runs.update()
                .where(swarm_runs.c.run_id == run_id)
                .values(status=request.status, error=request.error, finished_at=now())
            )
            if kind == "dispatch":
                view = self.dispatch_view(run_id)
                connection.execute(
                    swarm_runs.update()
                    .where(swarm_runs.c.run_id == run_id)
                    .values(snapshot=view.model_dump_json())
                )
                return []
            return self.cleanup_threads(connection, run_id)

    @staticmethod
    def cleanup_threads(connection, run_id: str | None = None) -> list[str]:
        query = (
            select(swarm_claims.c.thread_id)
            .select_from(swarm_claims)
            .join(swarm_runs, swarm_claims.c.run_id == swarm_runs.c.run_id)
            .join(attempts, swarm_claims.c.thread_id == attempts.c.thread_id)
            .where(
                swarm_runs.c.kind == "evaluation",
                swarm_runs.c.status != "running",
                attempts.c.status.in_(("paused", "running", "interrupted")),
            )
        )
        if run_id:
            query = query.where(swarm_runs.c.run_id == run_id)
        return list(connection.scalars(query))

    def dispatch_view(self, run_id: str) -> DispatchRun:
        with self.store.connection() as connection:
            run = (
                connection.execute(
                    select(swarm_runs).where(
                        swarm_runs.c.run_id == run_id, swarm_runs.c.kind == "dispatch"
                    )
                )
                .mappings()
                .one_or_none()
            )
            if run is None:
                raise HTTPException(404, "Dispatch not found")
            if run["snapshot"]:
                return DispatchRun.model_validate_json(run["snapshot"])
            version = self.store.find(connection, "run_configurations", run_id)
            claim = self.store.queries.claims_projection.subquery()
            rows = connection.execute(
                select(
                    swarm_claims.c.claim_id,
                    swarm_claims.c.status.label("work_status"),
                    claim.c.status,
                    claim.c.outcome,
                    claim.c.payout_amount,
                    claim.c.payment_status,
                )
                .join(claim, claim.c.claim_id == swarm_claims.c.claim_id)
                .where(swarm_claims.c.run_id == run_id)
                .order_by(swarm_claims.c.position)
            )
            items = []
            for row in rows:
                status = row.status
                if status == "decided" and row.payment_status == "unpaid":
                    status = "in_progress"
                if status == "waiting" and row.work_status in {"failed", "skipped"}:
                    status = row.work_status
                items.append(
                    DispatchItem(
                        claim_id=row.claim_id,
                        status=status,
                        outcome=row.outcome or None,
                        payout_amount=row.payout_amount,
                    )
                )
            usage, segments = Telemetry(self.store).for_run(run_id)
        metrics, elapsed_ms = measure(usage, segments, run["status"])
        costs = [claim.cost for claim in metrics.values() if claim.cost is not None]
        complete = all(not claim.missing_cost for claim in metrics.values())
        model_cost = str(sum(costs)) if costs or complete else None
        summary = None
        if run["status"] != "running":
            summary = DispatchSummary(
                approved=sum(item.outcome == "approve" for item in items),
                declined=sum(item.outcome == "decline" for item in items),
                referred=sum(item.outcome == "refer" for item in items),
                waiting_sign_off=[
                    HeldPayout(claim_id=item.claim_id, payout_amount=item.payout_amount)
                    for item in items
                    if item.status == "waiting_sign_off"
                    and item.payout_amount is not None
                ],
                needs_handler=[
                    item.claim_id for item in items if item.status == "needs_handler"
                ],
                in_progress=[
                    item.claim_id for item in items if item.status == "in_progress"
                ],
            )
        return DispatchRun(
            config_version=version["version"] if version else None,
            run_id=run_id,
            status=run["status"],
            concurrency=run["concurrency"],
            claims=items,
            summary=summary,
            model_cost=model_cost,
            model_cost_complete=complete,
            elapsed_ms=elapsed_ms,
            error=run["error"],
        )

    def current_dispatch(self) -> DispatchRun | None:
        with self.store.connection() as connection:
            newest = connection.scalar(
                select(swarm_runs.c.run_id)
                .where(swarm_runs.c.kind == "dispatch")
                .order_by(swarm_runs.c.created_at.desc(), swarm_runs.c.run_id)
                .limit(1)
            )
            return self.dispatch_view(newest) if newest else None

    def recover(self):
        with self.store.connection(write=True) as connection:
            stale = list(connection.scalars(select(swarm_runs.c.run_id).where(active)))
            for run_id in stale:
                self.close(
                    run_id,
                    RunFinish(
                        status="stopped",
                        error="Agent service restarted",
                        dispositions={},
                    ),
                )
            threads = self.cleanup_threads(connection)
        return {"evaluation_threads": threads}


def routes(swarm: Swarm) -> APIRouter:
    router = APIRouter()
    router.include_router(internal_routes(swarm))

    @router.post(
        "/api/dispatch",
        status_code=202,
        operation_id="start_dispatch",
        dependencies=[Depends(require_handler)],
    )
    def start_dispatch(body: StartDispatch) -> AcceptedRun:
        """Ask the agent service to start a dispatch."""
        return start("/dispatch", body)

    @router.post(
        "/api/evaluations",
        status_code=202,
        operation_id="start_evaluation",
        dependencies=[Depends(require_handler)],
    )
    def start_evaluation(body: StartEvaluation) -> AcceptedRun:
        """Ask the agent service to start a labelled evaluation."""
        return start("/evaluations", body)

    @router.get("/api/dispatch/current", operation_id="get_current_dispatch")
    def current_dispatch() -> DispatchRun | None:
        """Read the most recent dispatch and its stored progress."""
        return swarm.current_dispatch()

    @router.get("/api/dispatch/{run_id}", operation_id="get_dispatch")
    def get_dispatch(run_id: str) -> DispatchRun:
        """Read one dispatch by its accepted run ID."""
        return swarm.dispatch_view(run_id)

    return router


def internal_routes(swarm: Swarm) -> APIRouter:
    router = APIRouter(prefix="/api/swarm/runs")

    @router.post("", status_code=201, operation_id="create_agent_swarm_run")
    def create_swarm_run(body: RunInput) -> dict[str, str]:
        """Create the accepted run and save its ordered claim membership."""
        swarm.create(body)
        return {"run_id": body.run_id}

    @router.post(
        "/versions/{version}",
        status_code=201,
        operation_id="create_versioned_swarm_run",
    )
    def create_versioned_swarm_run(version: int, body: RunInput) -> dict[str, str]:
        """Save a run's bound configuration and claim membership together."""
        swarm.create(body, version)
        return {"run_id": body.run_id}

    @router.post("/recover", operation_id="recover_swarm_runs")
    def recover() -> dict[str, list[str]]:
        """Stop runs left open by the previous agent process."""
        return swarm.recover()

    @router.get("/{run_id}/work", operation_id="list_swarm_run_work")
    def list_work(run_id: str) -> list[str]:
        """Read the ordered claim IDs assigned to this run."""
        return swarm.claim_ids(run_id)

    @router.post(
        "/{run_id}/claims/{claim_id}/activate", operation_id="activate_swarm_claim"
    )
    def activate(run_id: str, claim_id: str) -> dict[str, str]:
        """Start or continue this run's claim attempt."""
        return swarm.activate(run_id, claim_id)

    @router.post(
        "/{run_id}/claims/{claim_id}/{thread_id}/finish",
        operation_id="finish_swarm_claim",
    )
    def finish(run_id: str, claim_id: str, thread_id: str, body: ClaimFinish):
        """Reconcile the claim attempt with the saved settlement and retry budget."""
        return swarm.finish(run_id, claim_id, thread_id, body.error)

    @router.post("/{run_id}/close", operation_id="close_swarm_run")
    def close(run_id: str, body: RunFinish) -> dict[str, list[str]]:
        """Close the run while preserving independently recoverable claims."""
        return {"evaluation_threads": swarm.close(run_id, body)}

    return router
