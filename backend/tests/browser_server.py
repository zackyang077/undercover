"""Isolated browser acceptance server; never used by the normal app."""

import os
import tempfile
import time
from pathlib import Path

from agent.main import run
from agent.tests.test_agent import StubModel, replies
from backend.main import create_app
from backend.tests.queue_stub import scripted_queue

TEMP = tempfile.TemporaryDirectory(prefix="undercover-browser-")
os.environ["UNDERCOVER_DB"] = str(Path(TEMP.name) / "browser.sqlite3")
ATTEMPTS: dict[str, int] = {}


def scripted_run(claim_id: str, thread_id: str) -> str:
    time.sleep(1.5 if claim_id == "CLM-1009" else 0.7)
    ATTEMPTS[claim_id] = ATTEMPTS.get(claim_id, 0) + 1
    if claim_id == "CLM-1012":
        raise RuntimeError("Simulated persistent provider failure")
    if claim_id in {"CLM-1006", "CLM-1010"} and ATTEMPTS[claim_id] == 1:
        raise RuntimeError("Simulated provider failure; retry this claim")
    scenarios: dict[str, tuple[float, str]] = {
        "CLM-1003": (3750, "approve"),
        "CLM-1004": (0, "decline"),
        "CLM-1005": (0, "refer"),
        "CLM-1006": (0, "refer"),
        "CLM-1007": (1000, "approve"),
        "CLM-1008": (1000.01, "approve"),
        "CLM-1009": (1250, "approve"),
        "CLM-1010": (0, "refer"),
    }
    amount, outcome = scenarios.get(claim_id, (420, "approve"))
    messages = replies(amount, outcome)
    return run(claim_id, thread_id, StubModel(messages=iter(messages)))


app = create_app(
    Path(TEMP.name) / "browser.sqlite3",
    scripted_run,
    scripted_queue(scripted_run, early=True),
)
