"""Local background runs and persisted, observable activity."""

import asyncio
from collections.abc import Callable
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

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
        with self.store.connection() as connection:
            active = connection.execute(
                "SELECT thread_id FROM runs WHERE status='running'"
            ).fetchall()
            for row in active:
                self.finish(
                    row["thread_id"],
                    "interrupted",
                    "Backend restarted; run was interrupted",
                )

    def list(self, claim_id: str) -> list[Run]:
        self.store.get("claims", claim_id)
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM runs WHERE claim_id=? ORDER BY started_at", (claim_id,)
            ).fetchall()
            return [
                Run(
                    **dict(row),
                    run_id=row["thread_id"],
                    events=[
                        Event(**dict(event))
                        for event in connection.execute(
                            "SELECT * FROM events WHERE thread_id=? ORDER BY id",
                            (row["thread_id"],),
                        ).fetchall()
                    ],
                )
                for row in rows
            ]

    def begin(self, connection, claim_id: str) -> str:
        active = connection.execute(
            "SELECT 1 FROM runs WHERE claim_id=? AND status='running'", (claim_id,)
        ).fetchone()
        if active or self.store.find(connection, "decisions", claim_id):
            raise HTTPException(
                409, "Claim already has active work or a final decision"
            )
        if self.store.failure_count(connection, claim_id) >= MAX_ATTEMPTS:
            raise HTTPException(409, "Claim is left for a handler after two failures")
        thread_id = str(uuid4())
        connection.execute(
            "INSERT INTO runs VALUES (?, ?, 'running', ?, '', '')",
            (thread_id, claim_id, now()),
        )
        connection.execute("INSERT INTO harness_attempts VALUES (?)", (thread_id,))
        connection.execute(
            "INSERT INTO events(thread_id,kind,tool_name,text,created_at) "
            "VALUES(?,'started','','Agent run started',?)",
            (thread_id, now()),
        )
        return thread_id

    def start(self, claim_id: str) -> Run:
        self.store.get("claims", claim_id)
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.begin(connection, claim_id)
        return self.list(claim_id)[-1]

    def event(self, thread_id: str, event: EventInput) -> dict[str, str]:
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM runs WHERE thread_id=?", (thread_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(404, "Run not found")
            if row["status"] != "running":
                raise HTTPException(409, "Run is no longer active")
            connection.execute(
                "INSERT INTO events(thread_id,kind,tool_name,text,created_at) "
                "VALUES(?,?,?,?,?)",
                (thread_id, event.kind, event.tool_name, event.text, now()),
            )
        return {"status": "recorded"}

    def finish(self, thread_id: str, status: str, error: str):
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE runs SET status=?, finished_at=?, error=? "
                "WHERE thread_id=? AND status='running'",
                (status, now(), error, thread_id),
            )
            if not changed.rowcount:
                return
            connection.execute(
                "INSERT INTO events(thread_id,kind,tool_name,text,created_at) "
                "VALUES(?,?,?,?,?)",
                (thread_id, status, "", error or "Agent run completed", now()),
            )

    async def execute(self, claim_id: str, thread_id: str):
        try:
            await asyncio.to_thread(self.runner, claim_id, thread_id)
        except Exception as error:
            self.finish(thread_id, "failed", self.error_text(error))
        else:
            self.finish(thread_id, "completed", "")

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
        with runs.store.connection() as connection:
            queue = runs.store.find(connection, "queue_runs", "current")
        if queue and queue["status"] == "running":
            raise HTTPException(409, "The queue is running; wait or stop it first")
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
