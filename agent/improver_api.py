"""The improvement service's private client for backend-owned round records."""

import os
from dataclasses import dataclass

import httpx
from fastapi import HTTPException

ROUNDS = "/api/improver/rounds"


def request(method: str, path: str, body=None):
    origin = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
    try:
        response = httpx.request(method, f"{origin}{path}", json=body, timeout=45)
    except httpx.RequestError as exc:
        raise HTTPException(
            503, "The round service could not reach the backend"
        ) from exc
    if response.is_error:
        raise HTTPException(response.status_code, response.json()["detail"])
    return response.json()


@dataclass(frozen=True)
class RoundClient:
    id: str

    def get(self, resource=""):
        return request("GET", f"{ROUNDS}/{self.id}{resource}")

    def post(self, resource, body):
        return request("POST", f"{ROUNDS}/{self.id}/{resource}", body)
