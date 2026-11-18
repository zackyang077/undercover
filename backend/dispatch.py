"""Wake the agent only after the calling business transaction has committed."""

import logging
import os

import httpx


def notify(path: str):
    url = os.getenv("UNDERCOVER_AGENT", "http://127.0.0.1:8002")
    try:
        response = httpx.post(f"{url.rstrip('/')}{path}", timeout=2)
        response.raise_for_status()
    except httpx.HTTPError:
        logging.getLogger(__name__).warning("Agent unavailable; work is saved")
