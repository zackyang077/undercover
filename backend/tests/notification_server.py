"""Observe browser-test run starts and committed sign-offs."""

import os
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import JsonValue

from backend.tests.auth_server import routes as auth_routes

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("UNDERCOVER_TEST_ORIGIN", "http://127.0.0.1:5174")],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(auth_routes)
observations: dict[str, list[dict[str, JsonValue]]] = {}
dispatch_starts: list[dict[str, JsonValue]] = []
evaluation_starts: list[dict[str, JsonValue]] = []


@app.get("/health", operation_id="notification_health")
def health() -> dict[str, str]:
    """Report readiness to the Playwright launcher."""
    return {"status": "ok"}


def record_start(
    starts: list[dict[str, JsonValue]], body: dict[str, JsonValue]
) -> dict[str, str]:
    run_id = str(uuid4())
    starts.append({"run_id": run_id, "request": body})
    return {"run_id": run_id}


@app.post("/dispatch", status_code=202, operation_id="observe_dispatch_start")
def observe_dispatch(body: dict[str, JsonValue]) -> dict[str, str]:
    """Accept a dispatch start for the browser test to play out through the API."""
    return record_start(dispatch_starts, body)


@app.get("/dispatch-observations", operation_id="read_dispatch_starts")
def read_dispatch_starts() -> list[dict[str, JsonValue]]:
    """Read the dispatch requests received by the test stand-in."""
    return dispatch_starts


@app.post("/evaluations", status_code=202, operation_id="observe_evaluation_start")
def observe_evaluation(body: dict[str, JsonValue]) -> dict[str, str]:
    """Accept an evaluation start for the browser test to replay through the API."""
    return record_start(evaluation_starts, body)


@app.get("/evaluation-observations", operation_id="read_evaluation_starts")
def read_evaluation_starts() -> list[dict[str, JsonValue]]:
    """Read the evaluation requests received by the test stand-in."""
    return evaluation_starts


@app.post("/claims/{claim_id}/resume", operation_id="observe_resume")
def observe_resume(claim_id: str) -> dict[str, str]:
    """Observe the sign-off as a separate service can read it."""
    base = os.environ["BACKEND_URL"]
    response = httpx.get(f"{base}/api/claims/{claim_id}", timeout=5)
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
