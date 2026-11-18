"""Run a snapshot of waiting claims sequentially in the local backend."""

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.finance import now
from backend.runs import Runs


class AllClaims(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["all"]


class CappedClaims(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["capped"]
    max_claims: Annotated[int, Field(gt=0, strict=True)]


QueueStart = Annotated[AllClaims | CappedClaims, Body(discriminator="mode")]


TodoStatus = Literal["pending", "in_progress", "done", "needs_handler"]


class Todo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    text: Annotated[str, Field(min_length=1, max_length=200)]
    status: TodoStatus


class Todos(BaseModel):
    items: list[Todo]


class AttemptEnd(BaseModel):
    error: str


class QueueItem(BaseModel):
    claim_id: str
    thread_id: str = ""
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    error: str = ""
    summary: str = ""


class QueueRun(BaseModel):
    queue_run_id: str
    status: Literal["running", "completed", "stopped", "interrupted", "failed"]
    stop_requested: bool
    created_at: str
    finished_at: str
    items: list[QueueItem]
    error: str
    thread_id: str = ""
    mode: Literal["all", "capped"] = "all"
    target: int = 0
    admitted: list[str] = Field(default_factory=list)
    todos: list[Todo] = Field(default_factory=list)
    summary: str = ""


ALL_CLAIMS = AllClaims(mode="all")


class QueueRuns:
    def __init__(self, runs: Runs, runner: Callable[[str, str], str] | None = None):
        self.runner = runner
        self.runs = runs
        self.store = runs.store
        self.tasks: set[asyncio.Task] = set()

    def current(self) -> QueueRun | None:
        with self.store.connection() as connection:
            saved = self.store.find(connection, "queue_runs", "current")
        return QueueRun.model_validate(saved) if saved else None

    def save(self, connection, run: QueueRun):
        body = run.model_dump(mode="json")
        if not self.store.update(connection, "queue_runs", "current", body).rowcount:
            self.store.insert(connection, "queue_runs", "current", body)

    @contextmanager
    def editing_connection(self, queue_run_id: str):
        with self.store.connection(write=True) as connection:
            saved = self.store.find(connection, "queue_runs", "current")
            if not saved or saved["queue_run_id"] != queue_run_id:
                raise HTTPException(404, "Queue run not found")
            batch = QueueRun.model_validate(saved)
            yield batch, connection
            self.save(connection, batch)

    @contextmanager
    def editing(self, queue_run_id: str) -> Iterator[QueueRun]:
        with self.editing_connection(queue_run_id) as (batch, _):
            yield batch

    @staticmethod
    def require_active(batch: QueueRun):
        if batch.status != "running" or batch.stop_requested:
            raise HTTPException(409, "Queue is finished or stopping")

    def start(self, request: AllClaims | CappedClaims = ALL_CLAIMS) -> QueueRun:
        with self.store.connection(write=True) as connection:
            current = self.store.find(connection, "queue_runs", "current")
            if current and current["status"] == "running":
                raise HTTPException(409, "The queue is already running")
            items = [
                QueueItem(claim_id=c.claim_id, summary=c.summary)
                for c in self.store.queue()
                if c.status == "waiting"
            ]
            if not items:
                raise HTTPException(409, "There are no waiting claims")
            run = QueueRun(
                queue_run_id=str(uuid4()),
                status="running",
                stop_requested=False,
                created_at=now(),
                finished_at="",
                items=items,
                error="",
                thread_id=str(uuid4()),
                mode=request.mode,
                target=min(request.max_claims, len(items))
                if isinstance(request, CappedClaims)
                else len(items),
            )
            self.save(connection, run)
        return run

    def admit(self, queue_run_id: str, claim_id: str) -> dict[str, str]:
        with self.editing_connection(queue_run_id) as (batch, connection):
            self.require_active(batch)
            item = next((i for i in batch.items if i.claim_id == claim_id), None)
            if not item:
                raise HTTPException(409, "Claim is outside this queue run")
            if any(i.status == "running" for i in batch.items):
                raise HTTPException(409, "Another helper is still running")
            if claim_id not in batch.admitted and len(batch.admitted) >= batch.target:
                raise HTTPException(409, "Claim cap reached")
            thread_id = self.runs.begin(connection, claim_id)
            if claim_id not in batch.admitted:
                batch.admitted.append(claim_id)
            item.thread_id, item.status = thread_id, "running"
            todo = next((t for t in batch.todos if t.claim_id == claim_id), None)
            if todo:
                todo.status = "in_progress"
            else:
                batch.todos.append(
                    Todo(
                        claim_id=claim_id,
                        text=f"Review {claim_id}",
                        status="in_progress",
                    )
                )
        return {"claim_id": claim_id, "thread_id": thread_id}

    def task_status(self, claim_id: str) -> TodoStatus:
        detail = self.store.detail(claim_id)
        if any(r.status == "running" for r in self.runs.list(claim_id)):
            return "in_progress"
        if detail.status in {"needs_handler", "waiting_sign_off"}:
            return "needs_handler"
        if detail.decision:
            handler = detail.decision["outcome"] == "refer" or (
                detail.decision["outcome"] == "approve" and not detail.payment
            )
            return "needs_handler" if handler else "done"
        return "pending"

    def write_todos(self, queue_run_id: str, request: Todos) -> QueueRun:
        with self.editing(queue_run_id) as batch:
            self.require_active(batch)
            ids = [t.claim_id for t in request.items]
            scope = {i.claim_id for i in batch.items}
            if len(set(ids)) != len(ids) or not set(ids) <= scope:
                raise HTTPException(409, "Tasks must name distinct claims in this run")
            if len(ids) > batch.target or not set(batch.admitted) <= set(ids):
                raise HTTPException(
                    409, "Tasks must preserve admitted work and the cap"
                )
            for task in request.items:
                if task.status != self.task_status(task.claim_id):
                    raise HTTPException(
                        409, "Task status must match the recorded claim"
                    )
            batch.todos = request.items
        return batch

    def finish_attempt(self, queue_run_id: str, thread_id: str, error: str) -> dict:
        batch = self.current()
        if not batch or batch.queue_run_id != queue_run_id:
            raise HTTPException(404, "Queue run not found")
        item = next((i for i in batch.items if i.thread_id == thread_id), None)
        if not item:
            raise HTTPException(404, "Attempt does not belong to this queue run")
        result = self.runs.conclude(item.claim_id, thread_id, error)
        with self.editing(queue_run_id) as batch:
            item = next(i for i in batch.items if i.thread_id == thread_id)
            item.status = "completed" if result.status == "completed" else "failed"
            item.error = result.error
            for task in batch.todos:
                if task.claim_id == item.claim_id:
                    task.status = self.task_status(item.claim_id)
        detail = self.store.detail(item.claim_id)
        decision = detail.decision
        line = (
            (
                f"{decision['outcome']} ${float(str(decision['payout_amount'])):.2f}; "
                f"{detail.status}"
            )
            if decision
            else result.error
        )
        return {
            "claim_id": item.claim_id,
            "status": self.task_status(item.claim_id),
            "summary": f"{item.claim_id}: {line}",
        }

    def check(self, queue_run_id: str) -> dict:
        batch = self.current()
        if not batch or batch.queue_run_id != queue_run_id:
            raise HTTPException(404, "Queue run not found")
        waiting = {c.claim_id for c in self.store.queue() if c.status == "waiting"}
        remaining = [
            i.claim_id
            for i in batch.items
            if i.claim_id in waiting and i.claim_id not in batch.admitted
        ]
        retries = [c for c in batch.admitted if c in waiting]
        active = [i.claim_id for i in batch.items if i.status == "running"]
        slots = max(0, batch.target - len(batch.admitted))
        return {
            "remaining": remaining if slots else [],
            "retries": retries,
            "unused_slots": slots,
            "active": active,
            "complete": not active and not retries and not (slots and remaining),
            "stop_requested": batch.stop_requested,
        }

    def stop(self, queue_run_id: str) -> QueueRun:
        with self.editing(queue_run_id) as run:
            if run.status != "running":
                raise HTTPException(409, "The queue is no longer running")
            run.stop_requested = True
        return run

    def finish(
        self,
        queue_run_id: str,
        status: Literal["completed", "stopped", "failed"],
        error: str = "",
        summary: str = "",
    ):
        with self.editing(queue_run_id) as run:
            if status == "completed" and not self.check(queue_run_id)["complete"]:
                raise RuntimeError(
                    "Queue finished with missed claims or pending retries"
                )
            for item in run.items:
                if (
                    item.claim_id not in run.admitted
                    and item.status == "pending"
                    and self.store.detail(item.claim_id).status
                    not in {"waiting", "in_progress"}
                ):
                    item.status = "skipped"
                    item.error = "Claim resolved outside this run"
            run.status = status
            run.finished_at = now()
            run.error = error
            run.summary = summary

    def recover(self):
        current = self.current()
        if current and current.status == "running":
            with self.editing(current.queue_run_id) as run:
                run.status = "interrupted"
                run.finished_at = now()
                run.error = (
                    "Backend restarted; start the queue to process remaining claims"
                )
                for item in run.items:
                    if item.status == "running":
                        item.status = "failed"
                        item.error = "Run interrupted by backend restart"
                for task in run.todos:
                    task.status = self.task_status(task.claim_id)

    async def execute(self, batch: QueueRun):
        try:
            if self.runner is None:
                raise RuntimeError("Queue agent is not configured")
            summary = await asyncio.to_thread(
                self.runner, batch.queue_run_id, batch.thread_id
            )
            current = self.current()
            status = "stopped" if current and current.stop_requested else "completed"
            self.finish(batch.queue_run_id, status, summary=summary)
        except Exception as error:
            current = self.current()
            if current:
                for item in current.items:
                    if item.status == "running":
                        self.finish_attempt(
                            batch.queue_run_id, item.thread_id, Runs.error_text(error)
                        )
            self.finish(batch.queue_run_id, "failed", Runs.error_text(error))

    def launch(self, batch: QueueRun):
        task = asyncio.create_task(self.execute(batch))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def close(self):
        current = self.current()
        if current and current.status == "running" and self.tasks:
            self.stop(current.queue_run_id)
        if self.tasks:
            await asyncio.gather(*self.tasks)


def routes(queue: QueueRuns) -> APIRouter:
    router = APIRouter()

    @router.get("/api/queue/runs/current", operation_id="get_queue_run")
    def get_queue_run() -> QueueRun | None:
        """Read the latest queue run and per-claim progress."""
        return queue.current()

    @router.post("/api/queue/runs", status_code=202, operation_id="start_queue_run")
    async def start_queue_run(request: QueueStart) -> QueueRun:
        """Start one sequential pass over the currently waiting claims."""
        run = queue.start(request)
        queue.launch(run)
        return run

    @router.post("/api/queue/runs/{queue_run_id}/stop", operation_id="stop_queue_run")
    def stop_queue_run(queue_run_id: str) -> QueueRun:
        """Stop the queue after the current claim finishes."""
        return queue.stop(queue_run_id)

    @router.put(
        "/api/queue/runs/{queue_run_id}/todos", operation_id="write_queue_todos"
    )
    def write_queue_todos(queue_run_id: str, request: Todos) -> QueueRun:
        """Save the queue agent's visible plan."""
        return queue.write_todos(queue_run_id, request)

    @router.post(
        "/api/queue/runs/{queue_run_id}/claims/{claim_id}/attempts",
        operation_id="admit_queue_claim",
    )
    def admit_queue_claim(queue_run_id: str, claim_id: str) -> dict[str, str]:
        """Admit and lock one helper attempt within the run cap."""
        return queue.admit(queue_run_id, claim_id)

    @router.get("/api/queue/runs/{queue_run_id}/check", operation_id="check_queue")
    def check_queue(queue_run_id: str) -> dict:
        """Find missed claims and pending retries within this run's cap."""
        return queue.check(queue_run_id)

    @router.post(
        "/api/queue/runs/{queue_run_id}/attempts/{thread_id}/finish",
        operation_id="finish_queue_attempt",
    )
    def finish_queue_attempt(
        queue_run_id: str, thread_id: str, request: AttemptEnd
    ) -> dict:
        """Verify a helper's saved result and release its attempt lock."""
        return queue.finish_attempt(queue_run_id, thread_id, request.error)

    return router
