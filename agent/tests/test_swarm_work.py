import json
from contextlib import nullcontext
from unittest.mock import Mock, call

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from agent import main
from agent.swarm_work import SwarmWork
from backend.main import create_app
from backend.tests.test_finance import decision

CLAIM_ID = "CLM-1001"
RUN_ID = "dispatch-1"
CLAIM_PATH = f"/api/swarm/runs/{RUN_ID}/claims/{CLAIM_ID}"


def test_claim_reserved_for_sign_off_skips_activation():
    api = Mock()
    claim_runner = Mock()
    work = SwarmWork(api, lambda _: nullcontext(False), claim_runner)

    assert work.work_claim(CLAIM_ID, RUN_ID) == "skipped"
    api.assert_not_called()
    claim_runner.assert_not_called()


def test_empty_activation_skips_claim():
    api = Mock(return_value={})
    claim_runner = Mock()
    work = SwarmWork(api, lambda _: nullcontext(True), claim_runner)

    assert work.work_claim(CLAIM_ID, RUN_ID) == "skipped"
    api.assert_called_once_with("POST", f"{CLAIM_PATH}/activate")
    claim_runner.assert_not_called()


def test_failed_attempt_gets_one_fresh_thread():
    api = Mock(
        side_effect=[
            {"thread_id": "first-thread"},
            {"retry": True, "status": "waiting"},
            {"thread_id": "second-thread"},
            {"retry": False, "status": "needs_handler"},
        ]
    )
    claim_runner = Mock(
        side_effect=[
            RuntimeError("No decision saved"),
            httpx.ReadTimeout("Backend slow"),
        ]
    )
    work = SwarmWork(api, lambda _: nullcontext(True), claim_runner)

    assert work.work_claim(CLAIM_ID, RUN_ID) == "needs_handler"
    assert claim_runner.call_args_list == [
        call(CLAIM_ID, "first-thread", run_id=RUN_ID),
        call(CLAIM_ID, "second-thread", run_id=RUN_ID),
    ]
    assert api.call_args_list == [
        call("POST", f"{CLAIM_PATH}/activate"),
        call(
            "POST", f"{CLAIM_PATH}/first-thread/finish", {"error": "No decision saved"}
        ),
        call("POST", f"{CLAIM_PATH}/activate"),
        call(
            "POST",
            f"{CLAIM_PATH}/second-thread/finish",
            {"error": "ReadTimeout: agent execution failed"},
        ),
    ]


@pytest.mark.parametrize("activated", [False, True])
def test_backend_api_timeout_is_not_a_model_timeout(activated):
    responses = [{"thread_id": "active-thread"}] if activated else []
    api = Mock(side_effect=[*responses, httpx.ReadTimeout("Backend slow")])
    claim_runner = Mock()
    work = SwarmWork(api, lambda _: nullcontext(True), claim_runner)

    with pytest.raises(httpx.ReadTimeout, match="Backend slow"):
        work.work_claim(CLAIM_ID, RUN_ID)
    assert api.call_count == len(responses) + 1
    assert claim_runner.call_count == len(responses)


def completion(name: str, arguments: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": name,
            "object": "chat.completion",
            "created": 1,
            "model": main.DEFAULT_MODEL,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": name,
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps(arguments),
                                },
                            }
                        ],
                    },
                }
            ],
        },
    )


@pytest.mark.parametrize("error_type", [ModelRateLimitError, ModelTimeoutError])
def test_provider_error_carries_same_thread_without_settling(
    tmp_path, monkeypatch, error_type
):
    db = tmp_path / "worker.sqlite3"
    monkeypatch.setenv("UNDERCOVER_DB", str(db))
    provider_calls = []

    def respond(request):
        provider_calls.append(json.loads(request.content)["messages"])
        if len(provider_calls) == 1:
            return completion("read_claim", {})
        if len(provider_calls) == 2:
            if error_type is ModelTimeoutError:
                raise httpx.ReadTimeout("Provider slow", request=request)
            return httpx.Response(429, json={"error": {"message": "Rate limit"}})
        return completion("record_decision", decision())

    app = create_app(db)
    with (
        TestClient(app) as client,
        httpx.Client(transport=httpx.MockTransport(respond)) as http_client,
    ):
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        thread_id = app.state.runs.start(CLAIM_ID).thread_id
        target = {"thread_id": thread_id}
        api = Mock(side_effect=[target, target, {"retry": False, "status": "decided"}])
        model = ChatOpenAI(
            model=main.DEFAULT_MODEL,
            base_url=main.OPENROUTER_BASE_URL,
            api_key=SecretStr("test-key"),
            use_responses_api=False,
            max_retries=0,
            http_client=http_client,
        )

        def run_claim(claim_id, thread_id, *, run_id):
            return main.run(claim_id, thread_id, llm=model, run_id=run_id)

        work = SwarmWork(api, lambda _: nullcontext(True), run_claim)
        with pytest.raises(error_type):
            work.work_claim(CLAIM_ID, RUN_ID)
        api.assert_called_once_with("POST", f"{CLAIM_PATH}/activate")
        assert len(provider_calls) == 2
        assert client.get(f"/api/claims/{CLAIM_ID}").json()["decision"] is None

        assert work.work_claim(CLAIM_ID, RUN_ID) == "decided"
        assert api.call_args_list == [
            call("POST", f"{CLAIM_PATH}/activate"),
            call("POST", f"{CLAIM_PATH}/activate"),
            call("POST", f"{CLAIM_PATH}/{thread_id}/finish", {"error": ""}),
        ]
        assert len(provider_calls) == 3
        assert provider_calls[1] == provider_calls[2]
        assert "Maya Patel" in json.dumps(provider_calls[2])
        assert client.get(f"/api/claims/{CLAIM_ID}").json()["payment"]["amount"] == 420
