import importlib.util
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx2 as httpx
import pytest
from openai import OpenAI

NOTEBOOK = Path(__file__).resolve().parents[2] / "labs" / "week1day2.ipynb"
VALID = json.dumps(
    {
        "rationale": "Synthetic policy covers this expense.",
        "decision": "approve",
        "payout": 25,
    }
)


def completion(content=VALID, *, finish_reason="stop", refusal=None, cost=0.01):
    return {
        "id": "test-completion",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "refusal": refusal,
                },
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 10,
            "total_tokens": 20,
            "cost": cost,
        },
    }


@pytest.fixture
def lab(tmp_path):
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    prefixes = ("import json\n", "class ClaimDecision(", "def decide(", "def is_right(")
    source = "\n\n".join(
        "".join(cell["source"])
        for cell in notebook["cells"]
        if cell["cell_type"] == "code" and "".join(cell["source"]).startswith(prefixes)
    )
    module_path = tmp_path / "claims_lab.py"
    module_path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("claims_lab", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__dict__.update(
        time=SimpleNamespace(monotonic=time.monotonic, sleep=Mock()),
        make_messages=lambda claim: [
            {"role": "system", "content": "Synthetic policy instructions."},
            {"role": "user", "content": json.dumps(claim)},
        ],
    )
    return module


@pytest.fixture
def stub(lab):
    clients = []

    def configure(responses):
        pending = iter(responses)
        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            status, body = next(pending)
            if isinstance(body, httpx.TransportError):
                raise body
            return httpx.Response(status, json=body)

        client = OpenAI(
            api_key="test-key",
            base_url="https://test.invalid/v1",
            max_retries=5,
            http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        )
        clients.append(client)
        lab.openrouter = client
        return requests

    yield configure
    for client in clients:
        client.close()


def test_success_requests_strict_schema_and_preserves_context(lab, stub):
    requests = stub([(200, completion())])
    answer = lab.decide({"claim_id": "TEST-1"}, "test-model", "low")
    assert answer["decision"] == "approve"
    assert answer["attempts"] == 1
    assert answer["error"] == ""
    assert answer["cost"] == 0.01
    request = requests[0]
    schema = request["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert set(schema["schema"]["required"]) == {"rationale", "decision", "payout"}
    assert request["provider"]["require_parameters"] is True
    assert request["reasoning_effort"] == "low"
    assert "Synthetic policy instructions." in request["messages"][0]["content"]
    assert "JSON" in request["messages"][0]["content"]
    lab.time.sleep.assert_not_called()


@pytest.mark.parametrize(
    "content",
    [
        "## Claim Assessment: TEST-1\nReview before finalizing.",
        '{"decision":"approve","payout":25}',
        '{"rationale":"Test","decision":"invalid","payout":25}',
        "",
        None,
    ],
)
def test_invalid_output_gets_validation_feedback_then_recovers(lab, stub, content):
    requests = stub([(200, completion(content)), (200, completion())])
    answer = lab.decide({"claim_id": "TEST-1"}, "test-model", "low")
    assert answer["decision"] == "approve"
    assert answer["attempts"] == 2
    assert answer["cost"] == 0.02
    messages = requests[1]["messages"]
    assert messages[:2] == requests[0]["messages"]
    assert messages[-2]["role"] == "assistant"
    assert messages[-1]["role"] == "user"
    assert "Invalid response" in messages[-1]["content"]
    assert "rationale, decision, and numeric payout" in messages[-1]["content"]
    lab.time.sleep.assert_called_once_with(1)


def test_fenced_json_does_not_need_another_paid_call(lab, stub):
    requests = stub([(200, completion(f"```json\n{VALID}\n```"))])
    assert lab.decide({"claim_id": "TEST-1"}, "test-model", "low")["payout"] == 25
    assert len(requests) == 1


def test_exhaustion_is_bounded_and_retains_total_cost(lab, stub, capsys):
    requests = stub([(200, completion("Markdown prose"))] * 3)
    answer = lab.decide({"claim_id": "TEST-1"}, "test-model", "low")
    assert answer["decision"] == "error"
    assert answer["attempts"] == len(requests) == 3
    assert answer["cost"] == 0.03
    assert "json_invalid" in answer["error"]
    assert [call.args[0] for call in lab.time.sleep.call_args_list] == [1, 2]
    assert "failed after 3 attempt(s)" in capsys.readouterr().out


@pytest.mark.parametrize("status", [408, 409, 429, 500, 503])
def test_transient_api_errors_retry(lab, stub, status):
    requests = stub(
        [
            (status, {"error": {"message": "Temporary failure"}}),
            (200, completion()),
        ]
    )
    answer = lab.decide({"claim_id": "TEST-1"}, "test-model", "low")
    assert answer["decision"] == "approve"
    assert len(requests) == answer["attempts"] == 2
    lab.time.sleep.assert_called_once_with(1)


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404])
def test_permanent_api_errors_fail_without_retry(lab, stub, status):
    requests = stub([(status, {"error": {"message": "Unavailable request"}})])
    answer = lab.decide({"claim_id": "TEST-1"}, "test-model", "low")
    assert answer["decision"] == "error"
    assert answer["attempts"] == len(requests) == 1
    assert f"API error {status}" in answer["error"]
    lab.time.sleep.assert_not_called()


def test_network_timeout_does_not_multiply_sdk_retries(lab, stub):
    requests = stub([(0, httpx.ReadTimeout("Timed out"))] * 3)
    answer = lab.decide({"claim_id": "TEST-1"}, "test-model", "low")
    assert answer["decision"] == "error"
    assert answer["attempts"] == len(requests) == 3
    assert "timed out" in answer["error"]


@pytest.mark.parametrize("finish_reason", ["length", "tool_calls"])
def test_incomplete_response_is_retried_even_if_json_is_valid(lab, stub, finish_reason):
    requests = stub(
        [
            (200, completion(finish_reason=finish_reason)),
            (200, completion()),
        ]
    )
    assert lab.decide({"claim_id": "TEST-1"}, "test-model", "low")["attempts"] == 2
    assert len(requests) == 2


@pytest.mark.parametrize("finish_reason", ["stop", "content_filter"])
def test_refusals_are_explicit_failures(lab, stub, finish_reason):
    requests = stub([(200, completion(refusal="Refused", finish_reason=finish_reason))])
    answer = lab.decide({"claim_id": "TEST-1"}, "test-model", "low")
    assert answer["decision"] == "error"
    assert answer["error"] == "Model refused the request"
    assert answer["cost"] == 0.01
    assert len(requests) == 1


def test_batch_continues_after_failure_without_counting_it_as_a_referral(
    lab, stub, capsys
):
    requests = stub([(200, completion("Prose"))] * 3 + [(200, completion())])
    claims = [{"claim_id": "TEST-1"}, {"claim_id": "TEST-2"}]
    labels = {
        claim["claim_id"]: {
            "difficulty": "synthetic",
            "decision": "approve",
            "payout": 25,
        }
        for claim in claims
    }
    answers = lab.run("test-model", "low", claims, labels)
    assert [answer["decision"] for answer in answers] == ["error", "approve"]
    assert [answer["right"] for answer in answers] == [False, True]
    assert len(requests) == 4
    assert "1 of 2 right, 1 failed" in capsys.readouterr().out


def test_attempt_budget_is_configurable_and_checked(lab, stub):
    requests = stub([(200, completion("Prose"))])
    with pytest.raises(ValueError, match="at least 1"):
        lab.decide({"claim_id": "TEST-1"}, "test-model", "low", max_attempts=0)
    assert not requests
    answer = lab.decide({"claim_id": "TEST-1"}, "test-model", "low", max_attempts=1)
    assert answer["decision"] == "error"
    assert len(requests) == 1
    lab.time.sleep.assert_not_called()
