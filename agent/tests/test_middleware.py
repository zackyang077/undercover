from unittest.mock import Mock

import httpx
import httpx2
import pytest
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from openai import APIStatusError

from agent.middleware import HarnessMiddleware, transient
from agent.tests.test_agent import StubModel


def test_model_retry_and_permanent_errors(monkeypatch):
    monkeypatch.setattr("agent.middleware.time.sleep", lambda _: None)
    middleware = HarnessMiddleware(180)
    calls = []
    request = ModelRequest(model=StubModel(messages=iter([])), messages=[])
    recovered = ModelResponse(result=[AIMessage(content="recovered")])

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("Temporary transport failure")
        return recovered

    assert middleware.wrap_model_call(request, handler) == recovered
    assert len(calls) == 2
    for code, retry in [(401, False), (429, True), (503, True)]:
        error = APIStatusError(
            "Provider error",
            response=httpx2.Response(
                code, request=httpx2.Request("GET", "https://example.test")
            ),
            body={},
        )
        assert transient(error) is retry
    with pytest.raises(ValueError, match="Permanent"):
        middleware.wrap_model_call(
            request, lambda _: (_ for _ in ()).throw(ValueError("Permanent"))
        )


def test_deadline_before_and_after_work(monkeypatch):
    clock = [0]
    monkeypatch.setattr("agent.middleware.time.monotonic", lambda: clock[0])
    middleware = HarnessMiddleware(180)
    tool_request = Mock(spec=ToolCallRequest)
    result = ToolMessage(content="done", tool_call_id="call")
    assert middleware.wrap_tool_call(tool_request, lambda _: result) == result
    request = ModelRequest(model=StubModel(messages=iter([])), messages=[])

    def late(request):
        clock[0] = 181
        return ModelResponse(result=[AIMessage(content="late")])

    with pytest.raises(TimeoutError):
        middleware.wrap_model_call(request, late)
    with pytest.raises(TimeoutError):
        middleware.wrap_tool_call(tool_request, lambda _: pytest.fail("Must not start"))
