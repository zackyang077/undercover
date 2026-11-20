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

from agent import improver, improver_api, main
from agent.checkpoints import clear_threads
from agent.claim_graph import WAITING_FOR_SIGN_OFF
from agent.configuration import RunConfiguration
from agent.dispatcher import MAX_CONCURRENCY, dispatch
from agent.ownership import ClaimOwnership
from agent.round_evaluation import RoundEvaluation
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


class RoundStart(BaseModel):
    handler_id: str


class RoundReview(RoundStart):
    proposal_id: str
    choice: Literal["approve", "reject"]
    reason: str


class ScheduledRun(StartDispatch):
    run_id: str
    kind: Literal["dispatch", "evaluation"]
    model: str
    reasoning_effort: str
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

    def run_claim(
        self, claim_id: str, thread_id: str, *, runner=None, **options
    ) -> str:
        self.waiting_threads.pop(claim_id, None)
        result = (runner or self.claim_runner)(claim_id, thread_id, **options)
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
        result = api("POST", f"{RUNS}/recover")
        clear_threads(result.get("evaluation_threads", []))
        api("POST", "/api/improver/recover", {})

    def evaluate_for_round(self, client, step):
        config = RunConfiguration.model_validate(
            api("GET", f"/api/agent/configuration/{step['version']}")
        )
        job = ScheduledRun(
            run_id=step["run_id"],
            kind="evaluation",
            model=config.model,
            reasoning_effort=config.reasoning_effort,
            dataset=step["dataset"],
            size="full",
            concurrency=step["concurrency"],
        )
        self.run_swarm(
            job, config, RoundEvaluation(client, job.run_id, step["cold_start"])
        )

    def improve(self, client, review):
        try:
            improver.run_round(client, self.evaluate_for_round, review)
        except Exception as error:
            if client.get()["status"] not in {"stopped", "finished"}:
                client.post("stop", {"reason": str(error)})
            raise

    def run_swarm(
        self,
        job: ScheduledRun,
        configuration: RunConfiguration,
        control: RoundEvaluation | None = None,
    ):
        api("POST", f"{RUNS}/versions/{configuration.version}", job.model_dump())
        path = f"{RUNS}/{job.run_id}"
        status, error, dispositions = "completed", "", {}
        try:
            claim_runner = (
                main.ClaimRunner(
                    configuration,
                    control.client.id if control else None,
                    control.before_model if control else None,
                )
                if self.can_resume_directly
                else self.claim_runner
            )
            if job.kind == "dispatch":
                claim_runner = partial(self.run_claim, runner=claim_runner)
            swarm = SwarmWork(
                api,
                self.ownership.dispatch,
                claim_runner,
                control.allowed if control else None,
            )
            claim_ids = api("GET", f"{path}/work")
            if control:
                dispositions = control.dispatch(
                    self.dispatch_runner, claim_ids, swarm.work_claim, job.concurrency
                )
                if not control.allowed():
                    status, error = "stopped", control.reason
            else:
                dispositions = self.dispatch_runner(
                    lambda: claim_ids, swarm.work_claim, job.run_id, job.concurrency
                )
        except Exception as exc:
            status, error = "failed", failure_text(exc)
        result = api(
            "POST",
            f"{path}/close",
            {"status": status, "error": error, "dispositions": dispositions},
        )
        clear_threads(result.get("evaluation_threads", []))
        return status


class ServiceTasks:
    def __init__(self, worker: Worker):
        self.worker = worker
        self.coordinator = ThreadPoolExecutor(max_workers=1, thread_name_prefix="swarm")
        self.approvals = ThreadPoolExecutor(max_workers=1, thread_name_prefix="signoff")
        self.standalone = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="standalone"
        )
        self.running = Lock()
        self.owner = ""
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

    def reserve(self, owner: str):
        if not self.ready.is_set():
            raise HTTPException(503, "The claim service is starting")
        with self.running:
            if self.owner:
                raise HTTPException(409, f"A {self.owner} is already running")
            self.owner = owner

    def release(self):
        with self.running:
            self.owner = ""

    def execute(self, function, *args):
        try:
            function(*args)
        finally:
            self.release()

    def improvement(self, body, round_id: str | None, background: BackgroundTasks):
        self.reserve("round")
        try:
            if round_id is None:
                record = improver_api.request("POST", improver_api.ROUNDS, body)
                client = improver_api.RoundClient(record["id"])
                review = None
            else:
                client = improver_api.RoundClient(round_id)
                record = client.post("review", body)
                review = body
            background.add_task(
                self.submit,
                self.coordinator,
                self.execute,
                self.worker.improve,
                client,
                review,
            )
            return record
        except Exception:
            self.release()
            raise

    def start_run(
        self, body: StartDispatch | StartEvaluation, background: BackgroundTasks
    ) -> dict[str, str]:
        self.reserve("evaluation" if isinstance(body, StartEvaluation) else "dispatch")
        try:
            configuration = RunConfiguration.model_validate(
                api("GET", "/api/agent/configuration/current")
            )
        except Exception:
            self.release()
            raise
        evaluation = isinstance(body, StartEvaluation)
        job = ScheduledRun(
            run_id=str(uuid4()),
            kind="evaluation" if evaluation else "dispatch",
            model=configuration.model,
            reasoning_effort=configuration.reasoning_effort,
            dataset=body.dataset if evaluation else None,
            size=body.size if evaluation else None,
            concurrency=body.concurrency,
        )

        background.add_task(
            self.submit,
            self.coordinator,
            self.execute,
            self.worker.run_swarm,
            job,
            configuration,
        )
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

    @app.post("/improvement/start", status_code=202, operation_id="start_improver_work")
    def start_round(body: RoundStart, background: BackgroundTasks):
        """Start a round from the handler's authenticated request."""
        return tasks.improvement(body.model_dump(), None, background)

    @app.post(
        "/improvement/{round_id}/review",
        status_code=202,
        operation_id="review_improver_proposal",
    )
    def review_proposal(round_id: str, body: RoundReview, background: BackgroundTasks):
        """Record a handler decision before resuming the round."""
        return tasks.improvement(body.model_dump(), round_id, background)

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
