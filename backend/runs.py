"""Local background runs and persisted, observable activity."""

import asyncio
from collections.abc import Callable
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from backend.db import attempts, events
from backend.db import runs as run_table
from backend.finance import now
from backend.storage import MAX_ATTEMPTS, Store


class EventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    tool_name: str
    text: str


class Event(EventInput):
    thread_id: str
    id: int
    created_at: str


class Run(BaseModel):
    run_id: str
    thread_id: str
    claim_id: str
    status: str
    started_at: str
    finished_at: str
    error: str
    events: list[Event]


class Runs:
    def __init__(self, store: Store, runner: Callable[[str, str], str]):
        self.store = store
        self.runner = runner
        self.tasks: set[asyncio.Task] = set()

    def recover(self):
        with self.store.connection(write=True) as connection:
            active = connection.scalars(
                select(run_table.c.thread_id).where(run_table.c.status == "running")
            ).all()
            for thread_id in active:
                self.finish(
                    thread_id, "interrupted", "Backend restarted; run was interrupted"
                )

    def list(self, claim_id: str) -> list[Run]:
        self.store.get("claims", claim_id)
        with self.store.connection() as connection:
            rows = (
                connection.execute(
                    select(run_table)
                    .where(run_table.c.claim_id == claim_id)
                    .order_by(run_table.c.started_at)
                )
                .mappings()
                .all()
            )
            by_thread: dict[str, list[Event]] = {row["thread_id"]: [] for row in rows}
            query = (
                select(events)
                .where(events.c.thread_id.in_(by_thread))
                .order_by(events.c.id)
            )
            for event in connection.execute(query).mappings():
                by_thread[event["thread_id"]].append(Event.model_validate(event))
            return [
                Run(**row, run_id=row["thread_id"], events=by_thread[row["thread_id"]])
                for row in rows
            ]

    def begin(self, connection, claim_id: str) -> str:
        latest = connection.execute(
            select(run_table.c.thread_id, run_table.c.status)
            .where(run_table.c.claim_id == claim_id)
            .order_by(run_table.c.started_at.desc())
            .limit(1)
        ).first()
        if (latest and latest.status == "running") or self.store.find(
            connection, "decisions", claim_id
        ):
            raise HTTPException(
                409, "Claim already has active work or a final decision"
            )
        if latest and latest.status == "interrupted":
            connection.execute(
                run_table.update()
                .where(run_table.c.thread_id == latest.thread_id)
                .values(status="running", finished_at="", error="")
            )
            return latest.thread_id
        if self.store.failure_count(connection, claim_id) >= MAX_ATTEMPTS:
            raise HTTPException(409, "Claim is left for a handler after two failures")
        thread_id = str(uuid4())
        connection.execute(
            run_table.insert().values(
                thread_id=thread_id,
                claim_id=claim_id,
                status="running",
                started_at=now(),
                finished_at="",
                error="",
            )
        )
        connection.execute(attempts.insert().values(thread_id=thread_id))
        connection.execute(
            events.insert().values(
                thread_id=thread_id,
                kind="started",
                tool_name="",
                text="Agent run started",
                created_at=now(),
            )
        )
        return thread_id

    def start(self, claim_id: str) -> Run:
        self.store.get("claims", claim_id)
        with self.store.connection(write=True) as connection:
            queue = self.store.find(connection, "queue_runs", "current")
            if queue and queue["status"] == "running":
                raise HTTPException(409, "The queue is running; wait or stop it first")
            self.begin(connection, claim_id)
        return self.list(claim_id)[-1]

    def event(self, thread_id: str, event: EventInput) -> dict[str, str]:
        with self.store.connection(write=True) as connection:
            status = connection.scalar(
                select(run_table.c.status).where(run_table.c.thread_id == thread_id)
            )
            if status is None:
                raise HTTPException(404, "Run not found")
            if status != "running":
                raise HTTPException(409, "Run is no longer active")
            connection.execute(
                events.insert().values(
                    thread_id=thread_id,
                    kind=event.kind,
                    tool_name=event.tool_name,
                    text=event.text,
                    created_at=now(),
                )
            )
        return {"status": "recorded"}

    def finish(self, thread_id: str, status: str, error: str):
        with self.store.connection(write=True) as connection:
            changed = connection.execute(
                run_table.update()
                .where(
                    run_table.c.thread_id == thread_id, run_table.c.status == "running"
                )
                .values(status=status, finished_at=now(), error=error)
            )
            if not changed.rowcount:
                return
            connection.execute(
                events.insert().values(
                    thread_id=thread_id,
                    kind=status,
                    tool_name="",
                    text=error
                    or (
                        "Waiting for handler sign-off"
                        if status == "paused"
                        else "Agent run completed"
                    ),
                    created_at=now(),
                )
            )

    def conclude(self, claim_id: str, thread_id: str, error: str) -> Run:
        with self.store.connection(write=True):
            detail = self.store.detail(claim_id)
            if not detail.decision:
                status = "failed"
                error = error or "The helper finished without a recorded decision"
            elif detail.status == "waiting_sign_off":
                status, error = "paused", ""
            elif detail.decision["outcome"] == "approve" and not detail.payment:
                status = "interrupted"
                error = error or "Decision saved; payment pending on this thread"
            else:
                status, error = "completed", ""
            self.finish(thread_id, status, error)
        return next(r for r in self.list(claim_id) if r.thread_id == thread_id)

    async def attempt(self, claim_id: str, thread_id: str) -> Run:
        error = ""
        try:
            await asyncio.to_thread(self.runner, claim_id, thread_id)
        except Exception as failure:
            error = self.error_text(failure)
        return self.conclude(claim_id, thread_id, error)

    async def execute(self, claim_id: str, thread_id: str):
        result = await self.attempt(claim_id, thread_id)
        if (
            result.status == "failed"
            and not self.store.detail(claim_id).decision
            and self.store.failures(claim_id) < MAX_ATTEMPTS
        ):
            retry = self.start(claim_id)
            await self.attempt(claim_id, retry.thread_id)

    @staticmethod
    def error_text(error: Exception) -> str:
        # Provider exceptions can include request details; keep those out of the UI.
        if isinstance(error, (RuntimeError, TimeoutError)):
            return str(error)
        return f"{type(error).__name__}: request failed; check model access and retry"

    def launch(self, claim_id: str, thread_id: str):
        task = asyncio.create_task(self.execute(claim_id, thread_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def close(self):
        if self.tasks:
            await asyncio.gather(*self.tasks)


def routes(runs: Runs) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/claims/{claim_id}/runs", status_code=202, operation_id="start_claim_run"
    )
    async def start_claim_run(claim_id: str) -> Run:
        """Start the agent on one waiting claim."""
        run = runs.start(claim_id)
        runs.launch(claim_id, run.thread_id)
        return run

    @router.get("/api/claims/{claim_id}/runs", operation_id="list_claim_runs")
    def list_claim_runs(claim_id: str) -> list[Run]:
        """Read run status and ordered claim activity."""
        return runs.list(claim_id)

    @router.post("/api/runs/{thread_id}/events", operation_id="record_run_event")
    def record_run_event(thread_id: str, event: EventInput) -> dict[str, str]:
        """Append an observable activity event to an active run."""
        return runs.event(thread_id, event)

    return router
