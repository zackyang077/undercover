"""Bound model calls and tools without abandoning active workers."""

from http import HTTPStatus

import httpx
from openai import APIConnectionError, APIStatusError

RETRY_DELAY = 0.25


def transient(error: Exception) -> bool:
    return isinstance(error, (httpx.TransportError, APIConnectionError)) or (
        isinstance(error, APIStatusError)
        and (
            error.status_code == HTTPStatus.TOO_MANY_REQUESTS
            or error.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
        )
    )
