"""Forward new run starts to the service that owns admission."""

import os
from http import HTTPStatus

import httpx
from fastapi import HTTPException
from pydantic import BaseModel


class AcceptedRun(BaseModel):
    run_id: str


def start(path: str, payload: BaseModel) -> AcceptedRun:
    url = os.getenv("UNDERCOVER_AGENT", "http://127.0.0.1:8002").rstrip("/")
    try:
        response = httpx.post(f"{url}{path}", json=payload.model_dump(), timeout=2)
    except httpx.RequestError as error:
        raise HTTPException(503, "The agent service is unavailable") from error
    if response.status_code != HTTPStatus.ACCEPTED:
        raise HTTPException(response.status_code, response.json()["detail"])
    return AcceptedRun.model_validate(response.json())
