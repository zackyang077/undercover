"""Inspect the provider payload and charges through ChatOpenAI."""

import json

import httpx
import pytest

from agent import improver
from agent.tests.test_improver import RoundFixture


@pytest.mark.parametrize("override", [None, "openrouter/free"])
def test_provider_receives_serial_tools_and_charge_is_reported(monkeypatch, override):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    if override:
        monkeypatch.setenv("MODEL_NAME", override)
    requests = []

    def reply(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "id": "completion-first",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "No proposal."},
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 4,
                    "total_tokens": 9,
                    "cost": 0 if override else 0.001,
                },
            },
        )

    adapter = improver.ChatOpenAI
    fixture = RoundFixture()
    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        monkeypatch.setattr(
            improver,
            "ChatOpenAI",
            lambda **options: adapter(http_client=client, **options),
        )
        improver.run_round(fixture, lambda *_: pytest.fail("Unexpected evaluation"))
    (payload,) = requests
    assert payload["model"] == (override or "openai/gpt-6-luna")
    assert payload["parallel_tool_calls"] is False
    if override:
        assert "reasoning_effort" not in payload
    else:
        assert payload["reasoning_effort"] == "medium"
    assert {tool["function"]["name"] for tool in payload["tools"]} == {
        "read_results",
        "propose_change",
        "run_evaluation",
        "propose_recipe",
    }
    assert [body["cost"] for action, body in fixture.writes if action == "usage"] == [
        0 if override else 0.001
    ]
