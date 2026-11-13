"""Run a snapshot of waiting claims sequentially in the local backend."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.finance import now
from backend.runs import Runs


class QueueItem(BaseModel):
    claim_id: str
    thread_id: str = ""
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    error: str = ""


class QueueRun(BaseModel):
    queue_run_id: str
    status: Literal["running", "completed", "stopped", "interrupted", "failed"]
    stop_requested: bool
    created_at: str
    finished_at: str
    items: list[QueueItem]
    error: str


class QueueRuns:
    def __init__(self, runs: Runs):
        self.runs = runs
        self.store = runs.store
        self.tasks: set[asyncio.Task] = set()

    def current(self) -> QueueRun | None:
        with self.store.connection() as connection:
            saved = self.store.find(connection, "queue_runs", "current")
        return QueueRun.model_validate(saved) if saved else None

    def save(self, connection, run: QueueRun):
        connection.execute(
            "INSERT INTO records VALUES ('queue_runs','current',?) "
            "ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body",
            (run.model_dump_json(),),
        )

    @contextmanager
    def editing(self, queue_run_id: str) -> Iterator[QueueRun]:
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = self.store.find(connection, "queue_runs", "current")
            if not run or run["queue_run_id"] != queue_run_id:
                raise HTTPException(404, "Queue run not found")
            batch = QueueRun.model_validate(run)
            yield batch
            self.save(connection, batch)

    def start(self) -> QueueRun:
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self.store.find(connection, "queue_runs", "current")
            if current and current["status"] == "running":
                raise HTTPException(409, "The queue is already running")
            items = [
                QueueItem(claim_id=c.claim_id)
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
            )
            self.save(connection, run)
        return run

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
    ):
        with self.editing(queue_run_id) as run:
            run.status = status
            run.finished_at = now()
            run.error = error

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

    async def process(self, queue_run_id: str, index: int, claim_id: str):
        try:
            claim_run = self.runs.start(claim_id)
        except HTTPException as error:
            if error.status_code != HTTPStatus.CONFLICT:
                raise
            with self.editing(queue_run_id) as batch:
                batch.items[index].status = "skipped"
                batch.items[index].error = str(error.detail)
            return
        with self.editing(queue_run_id) as batch:
            batch.items[index].status = "running"
            batch.items[index].thread_id = claim_run.thread_id
        await self.runs.execute(claim_id, claim_run.thread_id)
        result = next(
            r for r in self.runs.list(claim_id) if r.thread_id == claim_run.thread_id
        )
        with self.editing(queue_run_id) as batch:
            batch.items[index].status = (
                "completed" if result.status == "completed" else "failed"
            )
            batch.items[index].error = result.error

    async def execute(self, batch: QueueRun):
        try:
            for index, item in enumerate(batch.items):
                current = self.current()
                if current and current.stop_requested:
                    self.finish(batch.queue_run_id, "stopped")
                    return
                await self.process(batch.queue_run_id, index, item.claim_id)
        except Exception as error:
            self.finish(batch.queue_run_id, "failed", Runs.error_text(error))
        else:
            self.finish(batch.queue_run_id, "completed")

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
    async def start_queue_run() -> QueueRun:
        """Start one sequential pass over the currently waiting claims."""
        run = queue.start()
        queue.launch(run)
        return run

    @router.post("/api/queue/runs/{queue_run_id}/stop", operation_id="stop_queue_run")
    def stop_queue_run(queue_run_id: str) -> QueueRun:
        """Stop the queue after the current claim finishes."""
        return queue.stop(queue_run_id)

    return router
