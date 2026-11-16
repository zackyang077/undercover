"""Bound model calls and tools without abandoning active workers."""

import time
from http import HTTPStatus

import httpx
from langchain.agents.middleware import AgentMiddleware
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


class HarnessMiddleware(AgentMiddleware):
    def __init__(self, seconds: float):
        self.deadline = time.monotonic() + seconds

    def check(self):
        if time.monotonic() > self.deadline:
            raise TimeoutError("Agent exceeded its work deadline")

    def wrap_model_call(self, request, handler):
        self.check()
        try:
            response = handler(request)
        except Exception as error:
            if not transient(error):
                raise
            self.check()
            time.sleep(RETRY_DELAY)
            self.check()
            response = handler(request)
        self.check()
        return response

    def wrap_tool_call(self, request, handler):
        self.check()
        result = handler(request)
        self.check()
        return result
