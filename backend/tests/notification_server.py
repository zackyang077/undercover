"""Record which committed sign-offs the browser-test backend announces."""

import httpx
from fastapi import FastAPI
from pydantic import JsonValue

app = FastAPI()
observations: dict[str, list[dict[str, JsonValue]]] = {}


@app.get("/health", operation_id="notification_health")
def health() -> dict[str, str]:
    """Report readiness to the Playwright launcher."""
    return {"status": "ok"}


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
