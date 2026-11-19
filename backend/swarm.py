"""Persist dispatch membership and admit durable claim work."""

import json
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Column, Index, Integer, Table, Text, select, true

from backend.db import events, metadata, records
from backend.db import runs as attempts
from backend.finance import now
from backend.runs import Runs
from backend.storage import MAX_ATTEMPTS

swarm_runs = Table(
    "swarm_runs",
    metadata,
    Column("run_id", Text, primary_key=True),
    Column("kind", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("concurrency", Integer, nullable=False),
    Column("model", Text, nullable=False),
    Column("reasoning_effort", Text),
    Column("dataset", Text),
    Column("size", Text),
    Column("created_at", Text, nullable=False),
    Column("finished_at", Text),
    Column("error", Text, nullable=False),
    Column("snapshot", Text),
)
active = swarm_runs.c.status == "running"
Index(
    "one_active_swarm_run",
    swarm_runs.c.status,
    unique=True,
    sqlite_where=active,
    postgresql_where=active,
)
swarm_claims = Table(
    "swarm_claims",
    metadata,
    Column("run_id", Text, primary_key=True),
    Column("claim_id", Text, primary_key=True),
    Column("position", Integer, nullable=False),
    Column("thread_id", Text, index=True),
    Column("status", Text, nullable=False),
)

Concurrency = Annotated[int, Field(strict=True, ge=1, le=25)]


class StartDispatch(BaseModel):
    concurrency: Concurrency


class RunInput(StartDispatch):
    run_id: str
    kind: Literal["dispatch", "evaluation"]
    model: str
    reasoning_effort: str | None
    dataset: Literal["evaluation", "held_out"] | None
    size: Literal["full", "tiny"] | None


class ClaimFinish(BaseModel):
    error: str


class RunFinish(BaseModel):
    status: Literal["completed", "failed", "stopped"]
    error: str
    dispositions: dict[str, str]


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

    def create(self, request: RunInput):
        with self.store.connection(write=True) as connection:
            if connection.scalar(
                select(swarm_runs.c.run_id).where(swarm_runs.c.run_id == request.run_id)
            ):
                return
            if connection.scalar(select(swarm_runs.c.run_id).where(active)):
                raise HTTPException(409, "A dispatch or evaluation is already running")
            if request.kind != "dispatch" or request.dataset or request.size:
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
            if not connection.scalar(select(swarm_claims.c.claim_id).where(member)):
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
            connection.execute(
                swarm_claims.update().where(member).values(status=disposition)
            )
        return {"retry": retry, "status": disposition}

    def close(self, run_id: str, request: RunFinish):
        with self.store.connection(write=True) as connection:
            if not connection.scalar(
                select(swarm_runs.c.run_id).where(swarm_runs.c.run_id == run_id, active)
            ):
                return
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
        return {"evaluation_threads": []}


def routes(swarm: Swarm) -> APIRouter:
    router = APIRouter(prefix="/api/swarm/runs")

    @router.post("", status_code=201, operation_id="create_agent_swarm_run")
    def create_swarm_run(body: RunInput) -> dict[str, str]:
        """Create the accepted run and save its ordered claim membership."""
        swarm.create(body)
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
    def close(run_id: str, body: RunFinish) -> dict[str, str]:
        """Close the run while preserving independently recoverable claims."""
        swarm.close(run_id, body)
        return {"status": "recorded"}

    return router
