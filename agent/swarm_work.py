"""Advance and settle one claim on behalf of a dispatch or evaluation."""

from collections.abc import Callable
from contextlib import AbstractContextManager

from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError

from agent import main
from agent.checkpoints import clear_threads


def failure_text(error: Exception) -> str:
    if isinstance(error, (RuntimeError, TimeoutError)):
        return str(error)
    return f"{type(error).__name__}: agent execution failed"


class SwarmWork:
    def __init__(
        self,
        api: Callable[..., dict],
        reserve: Callable[[str], AbstractContextManager[bool]],
        claim_runner: Callable[..., str] = main.run,
    ):
        self.api = api
        self.reserve = reserve
        self.claim_runner = claim_runner

    def work_claim(self, claim_id: str, run_id: str) -> str:
        """Run up to two durable attempts under the service's claim reservation."""
        path = f"/api/swarm/runs/{run_id}/claims/{claim_id}"
        status = "skipped"
        with self.reserve(claim_id) as owned:
            if not owned:
                return status
            for _ in range(2):
                target = self.api("POST", f"{path}/activate")
                if not target:
                    break
                thread_id = target["thread_id"]
                error = ""
                try:
                    self.claim_runner(claim_id, thread_id, run_id=run_id)
                except (ModelRateLimitError, ModelTimeoutError):
                    raise
                except Exception as failure:
                    error = failure_text(failure)
                result = self.api(
                    "POST", f"{path}/{thread_id}/finish", {"error": error}
                )
                if result.get("clear_checkpoint"):
                    clear_threads([thread_id])
                status = result["status"]
                if not result["retry"]:
                    break
        return status
