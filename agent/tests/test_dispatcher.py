from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest
from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError

from agent import dispatcher


@pytest.mark.parametrize("concurrency", [1, 3, 25])
def test_dispatch_caps_concurrent_workers(concurrency):
    claims = [f"claim-{index}" for index in reversed(range(26))]
    order: list[str] = []
    active = 0
    peak = 0
    lock = Lock()
    full, overflow, release = Event(), Event(), Event()

    def work_claim(claim_id: str, run_id: str) -> str:
        nonlocal active, peak
        assert run_id == "run-1"
        with lock:
            order.append(claim_id)
            active += 1
            peak = max(peak, active)
            if active == concurrency:
                full.set()
            if active > concurrency:
                overflow.set()
        try:
            assert release.wait(timeout=5)
            return "completed"
        finally:
            with lock:
                active -= 1

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            dispatcher.dispatch, lambda: claims, work_claim, "run-1", concurrency
        )
        try:
            assert full.wait(timeout=5)
            assert not overflow.wait(timeout=0.1)
        finally:
            release.set()
        assert future.result(timeout=5) == dict.fromkeys(claims, "completed")

    assert peak == concurrency
    if concurrency == 1:
        assert order == claims


def test_dispatch_handles_provider_exhaustion_without_stopping_siblings(monkeypatch):
    monkeypatch.setattr(
        dispatcher,
        "WORK_RETRY",
        dispatcher.WORK_RETRY._replace(initial_interval=0.01, jitter=False),
    )
    calls: Counter[str] = Counter()
    lock = Lock()
    claims = ["rate-limit", "timeout", "referral", "recover", "needs-handler", "error"]

    def work_claim(claim_id: str, _run_id: str) -> str:
        with lock:
            calls[claim_id] += 1
            count = calls[claim_id]
        if claim_id == "rate-limit" or (claim_id == "recover" and count == 1):
            raise ModelRateLimitError("Too many requests")
        if claim_id == "timeout":
            raise ModelTimeoutError("Provider timed out")
        if claim_id == "error":
            raise ValueError("Unexpected worker error")
        return "needs_handler" if claim_id == "needs-handler" else "completed"

    assert dispatcher.dispatch(lambda: claims, work_claim, "run-2", 3) == {
        "rate-limit": "in_progress",
        "timeout": "in_progress",
        "referral": "completed",
        "recover": "completed",
        "needs-handler": "needs_handler",
        "error": "failed",
    }
    assert calls == {
        "rate-limit": 3,
        "timeout": 3,
        "referral": 1,
        "recover": 2,
        "needs-handler": 1,
        "error": 1,
    }


def test_empty_dispatch():
    def work_claim(_claim_id: str, _run_id: str) -> str:
        pytest.fail("An empty dispatch has no work")

    assert dispatcher.dispatch(list, work_claim, "empty", 3) == {}


@pytest.mark.parametrize("concurrency", [0, 26])
def test_dispatch_rejects_invalid_concurrency(concurrency):
    with pytest.raises(ValueError, match="between 1 and 25"):
        dispatcher.dispatch(list, lambda *_: "completed", "invalid", concurrency)
