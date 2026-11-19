"""Observe browser-test run starts and committed sign-offs."""

from uuid import uuid4

import httpx
from fastapi import FastAPI
from pydantic import JsonValue

app = FastAPI()
observations: dict[str, list[dict[str, JsonValue]]] = {}
dispatch_starts: list[dict[str, JsonValue]] = []


@app.get("/health", operation_id="notification_health")
def health() -> dict[str, str]:
    """Report readiness to the Playwright launcher."""
    return {"status": "ok"}


@app.post("/dispatch", status_code=202, operation_id="observe_dispatch_start")
def observe_dispatch(body: dict[str, JsonValue]) -> dict[str, str]:
    """Accept a dispatch start for the browser test to play out through the API."""
    run_id = str(uuid4())
    dispatch_starts.append({"run_id": run_id, "request": body})
    return {"run_id": run_id}


@app.get("/dispatch-observations", operation_id="read_dispatch_starts")
def read_dispatch_starts() -> list[dict[str, JsonValue]]:
    """Read the dispatch requests received by the test stand-in."""
    return dispatch_starts


@app.post("/claims/{claim_id}/resume", operation_id="observe_resume")
def observe_resume(claim_id: str) -> dict[str, str]:
    """Observe the sign-off as a separate service can read it."""
    response = httpx.get(f"http://127.0.0.1:8010/api/claims/{claim_id}", timeout=5)
    response.raise_for_status()
    detail = response.json()
    observations.setdefault(claim_id, []).append(
        {"status": detail["status"], "sign_off": detail["sign_off"]}
    )
    return {"status": "accepted"}


@app.get("/observations/{claim_id}", operation_id="read_observations")
def read_observations(claim_id: str) -> list[dict[str, JsonValue]]:
    """Read the sign-offs seen by the test stand-in."""
    return observations.get(claim_id, [])
