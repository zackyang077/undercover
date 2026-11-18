"""Run and resume claim threads outside the claims API process."""

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from threading import Lock

import httpx
from fastapi import FastAPI

from agent import main
from agent.queue import run_queue

log = logging.getLogger(__name__)


def api(method: str, path: str, body=None):
    with main.backend() as client:
        response = client.request(method, path, json=body)
        response.raise_for_status()
        return response.json()


def failure_text(exc: Exception) -> str:
    if isinstance(exc, (RuntimeError, TimeoutError)):
        return str(exc)
    return f"{type(exc).__name__}: agent execution failed"


class Worker:
    def __init__(self, claim_runner: Callable = main.run, queue_runner=run_queue):
        self.claim_runner = claim_runner
        self.queue_runner = queue_runner
        self.claim_locks: dict[str, Lock] = {}
        self.lock = Lock()

    def claim_lock(self, claim_id: str) -> Lock:
        with self.lock:
            return self.claim_locks.setdefault(claim_id, Lock())

    def claim(self, claim_id: str, thread_id: str) -> str:
        with self.claim_lock(claim_id):
            return self.claim_runner(claim_id, thread_id)

    def resume(self, claim_id: str):
        with self.claim_lock(claim_id):
            target = api("POST", f"/api/work/{claim_id}/activate")
            if not target:
                return
            if self.process(target):
                retry = api("POST", f"/api/work/{claim_id}/activate")
                if retry:
                    self.process(retry)

    def process(self, target):
        claim_id = target["claim_id"]
        thread = target["thread_id"]
        error = ""
        try:
            self.claim_runner(claim_id, thread)
        except Exception as exc:
            error = failure_text(exc)
        return api("POST", f"/api/work/{claim_id}/{thread}/finish", {"error": error})[
            "retry"
        ]

    def drain(self):
        work = api("GET", "/api/work")
        for claim_id in work["claims"]:
            self.resume(claim_id)
        batch = api("GET", "/api/queue/runs/current")
        if not batch or batch["status"] != "running":
            return
        status, summary, error = "completed", "", ""
        try:
            if not batch["stop_requested"]:
                summary = self.queue_runner(
                    batch["queue_run_id"], batch["thread_id"], claim_runner=self.claim
                )
            latest = api("GET", "/api/queue/runs/current")
            if latest["stop_requested"]:
                status = "stopped"
        except Exception as exc:
            status, error = "failed", failure_text(exc)
        api(
            "POST",
            f"/api/work/queue/{batch['queue_run_id']}/close",
            {"status": status, "error": error, "summary": summary},
        )


async def when_backend_ready(wake):
    while True:
        try:
            await asyncio.to_thread(api, "GET", "/api/health")
        except httpx.HTTPError:
            await asyncio.sleep(0.5)
        else:
            wake()
            return


def create_service(worker: Worker | None = None):
    worker = worker or Worker()
    coordinator = ThreadPoolExecutor(max_workers=1, thread_name_prefix="queue")
    approvals = ThreadPoolExecutor(max_workers=1, thread_name_prefix="signoff")

    def submit(pool, fn, *args):
        def execute():
            try:
                fn(*args)
            except Exception:
                log.exception("Claim service could not finish scheduled work")

        pool.submit(execute)

    def wake():
        submit(coordinator, worker.drain)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        task = asyncio.create_task(when_backend_ready(wake))
        yield
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(coordinator.shutdown)
        await asyncio.to_thread(approvals.shutdown)

    app = FastAPI(title="UnderCover Agent", lifespan=lifespan)

    @app.get("/health", operation_id="agent_health")
    def health():
        """Report agent service availability."""
        return {"status": "ok"}

    @app.post("/wake", status_code=202, operation_id="wake_agent")
    def start():
        """Continue outstanding work and process the active queue."""
        wake()
        return {"status": "accepted"}

    @app.post("/claims/{claim_id}/resume", status_code=202, operation_id="resume_claim")
    def resume(claim_id: str):
        """Schedule a signed claim on its saved thread."""
        submit(approvals, worker.resume, claim_id)
        return {"status": "accepted"}

    return app


app = create_service()
