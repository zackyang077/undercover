"""Forward browser-test approvals to the agent's backend round operations."""

import os

import httpx
from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import JsonValue

routes = APIRouter()


def forward(path: str, body: dict[str, JsonValue]) -> Response:
    result = httpx.post(
        f"{os.environ['BACKEND_URL']}/api/improver/rounds{path}",
        json=body,
        timeout=10,
    )
    return Response(result.content, result.status_code, media_type="application/json")


@routes.post("/improvement/start", operation_id="fixture_start_round")
def start_round(body: dict[str, JsonValue]) -> Response:
    """Create a round the browser test will advance through real backend APIs."""
    return forward("", body)


@routes.post("/improvement/{round_id}/review", operation_id="fixture_review_round")
def review_round(round_id: str, body: dict[str, JsonValue]) -> Response:
    """Record the signed-in handler's decision on the current proposal."""
    return forward(f"/{round_id}/review", body)
