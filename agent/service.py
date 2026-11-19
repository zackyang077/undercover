"""Run and resume claim threads outside the claims API process."""

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial
from threading import Event, Lock
from typing import Annotated, Literal
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent import main
from agent.claim_graph import WAITING_FOR_SIGN_OFF
from agent.dispatcher import MAX_CONCURRENCY, dispatch
from agent.ownership import ClaimOwnership
from agent.swarm_work import SwarmWork, failure_text

log = logging.getLogger(__name__)
RUNS = "/api/swarm/runs"

Dataset = Literal["evaluation", "held_out"]
EvaluationSize = Literal["full", "tiny"]


class StartDispatch(BaseModel):
    concurrency: Annotated[int, Field(strict=True, ge=1, le=MAX_CONCURRENCY)]


class StartEvaluation(StartDispatch):
    dataset: Dataset
    size: EvaluationSize


class ScheduledRun(StartDispatch):
    run_id: str
    kind: Literal["dispatch", "evaluation"]
    model: str
    reasoning_effort: Literal["low"] | None
    dataset: Dataset | None
    size: EvaluationSize | None


def api(method: str, path: str, body=None):
    with main.backend() as client:
        response = client.request(method, path, json=body)
        response.raise_for_status()
        return response.json()


class Worker:
    def __init__(self, claim_runner: Callable = main.run, dispatch_runner=dispatch):
        self.claim_runner = claim_runner
        self.can_resume_directly = claim_runner is main.run
        self.dispatch_runner = dispatch_runner
        self.ownership = ClaimOwnership()
        self.waiting_threads: dict[str, str] = {}

    def run_claim(self, claim_id: str, thread_id: str, **options) -> str:
        self.waiting_threads.pop(claim_id, None)
        result = self.claim_runner(claim_id, thread_id, **options)
        if self.can_resume_directly and result == WAITING_FOR_SIGN_OFF:
            self.waiting_threads[claim_id] = thread_id
        return result

    def resume(self, claim_id: str):
        if self.ownership.request_resume(claim_id):
            self.resume_reserved(claim_id)

    def resume_reserved(self, claim_id: str):
        with self.ownership.resume(claim_id):
            if waiting := self.waiting_threads.get(claim_id):
                saved = api("GET", f"/api/work/{claim_id}/signed-thread")
                if saved and saved["thread_id"] == waiting:
                    self.waiting_threads.pop(claim_id)
                    self.process({"claim_id": claim_id, **saved})
                    return
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
            if signed := target.get("sign_off"):
                self.claim_runner(claim_id, thread, signed_record=signed)
            else:
                self.run_claim(claim_id, thread)
        except Exception as exc:
            error = failure_text(exc)
        return api("POST", f"/api/work/{claim_id}/{thread}/finish", {"error": error})[
            "retry"
        ]

    def drain(self):
        work = api("GET", "/api/work")
        for claim_id in work["claims"]:
            self.resume(claim_id)

    def recover(self):
        api("POST", f"{RUNS}/recover")

    def run_swarm(self, job: ScheduledRun):
        api("POST", RUNS, job.model_dump())
        path = f"{RUNS}/{job.run_id}"
        status, error, dispositions = "completed", "", {}
        try:
            claim_runner = self.run_claim
            if self.can_resume_directly:
                settings = main.ModelSettings(job.model, job.reasoning_effort)
                claim_runner = partial(claim_runner, llm=main.model(settings))
            swarm = SwarmWork(api, self.ownership.dispatch, claim_runner)
            dispositions = self.dispatch_runner(
                lambda: api("GET", f"{path}/work"),
                swarm.work_claim,
                job.run_id,
                job.concurrency,
            )
        except Exception as exc:
            status, error = "failed", failure_text(exc)
        api(
            "POST",
            f"{path}/close",
            {"status": status, "error": error, "dispositions": dispositions},
        )


class ServiceTasks:
    def __init__(self, worker: Worker):
        self.worker = worker
        self.coordinator = ThreadPoolExecutor(max_workers=1, thread_name_prefix="swarm")
        self.approvals = ThreadPoolExecutor(max_workers=1, thread_name_prefix="signoff")
        self.standalone = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="standalone"
        )
        self.running = Lock()
        self.ready = Event()

    @staticmethod
    def submit(pool, fn, *args):
        def execute():
            try:
                fn(*args)
            except Exception:
                log.exception("Claim service could not finish scheduled work")

        pool.submit(execute)

    def wake(self):
        if self.ready.is_set():
            self.submit(self.standalone, self.worker.drain)

    def resume(self, claim_id: str):
        if self.worker.ownership.request_resume(claim_id):
            self.submit(self.approvals, self.worker.resume_reserved, claim_id)

    async def recover(self) -> bool:
        try:
            await asyncio.to_thread(self.worker.recover)
        except httpx.HTTPError:
            return False
        self.ready.set()
        self.wake()
        return True

    async def retry_recovery(self):
        while not await self.recover():
            await asyncio.sleep(0.5)

    @asynccontextmanager
    async def lifespan(self, application: FastAPI):
        task = (
            None if await self.recover() else asyncio.create_task(self.retry_recovery())
        )
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await asyncio.gather(
                asyncio.to_thread(self.coordinator.shutdown),
                asyncio.to_thread(self.approvals.shutdown),
                asyncio.to_thread(self.standalone.shutdown),
            )

    def execute_run(self, job: ScheduledRun):
        try:
            self.worker.run_swarm(job)
        finally:
            self.running.release()

    def start_run(
        self, body: StartDispatch | StartEvaluation, background: BackgroundTasks
    ) -> dict[str, str]:
        if not self.ready.is_set():
            raise HTTPException(503, "The claim service is starting")
        if not self.running.acquire(blocking=False):
            raise HTTPException(409, "A dispatch or evaluation is already running")
        settings = main.model_settings()
        evaluation = isinstance(body, StartEvaluation)
        job = ScheduledRun(
            run_id=str(uuid4()),
            kind="evaluation" if evaluation else "dispatch",
            model=settings.model_name,
            reasoning_effort=settings.reasoning_effort,
            dataset=body.dataset if evaluation else None,
            size=body.size if evaluation else None,
            concurrency=body.concurrency,
        )

        background.add_task(self.submit, self.coordinator, self.execute_run, job)
        return {"run_id": job.run_id}


def create_service(worker: Worker | None = None):
    tasks = ServiceTasks(worker or Worker())
    app = FastAPI(title="UnderCover Agent", lifespan=tasks.lifespan)

    @app.get("/health", operation_id="agent_health")
    def health():
        """Report agent service availability."""
        return {"status": "ok"}

    @app.post("/dispatch", status_code=202, operation_id="start_agent_dispatch")
    def start_dispatch(body: StartDispatch, background: BackgroundTasks):
        """Schedule the live claims dispatch."""
        return tasks.start_run(body, background)

    @app.post("/evaluations", status_code=202, operation_id="start_agent_evaluation")
    def start_evaluation(body: StartEvaluation, background: BackgroundTasks):
        """Schedule an evaluation of the selected claims."""
        return tasks.start_run(body, background)

    @app.post("/wake", status_code=202, operation_id="wake_agent")
    def start():
        """Continue outstanding standalone work."""
        tasks.wake()
        return {"status": "accepted"}

    @app.post("/claims/{claim_id}/resume", status_code=202, operation_id="resume_claim")
    def resume(claim_id: str):
        """Schedule a signed claim on its saved thread."""
        tasks.resume(claim_id)
        return {"status": "accepted"}

    return app


app = create_service()
