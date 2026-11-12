from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError

from agent import main
from backend.main import create_app
from backend.tests.test_finance import decision


class StubModel(GenericFakeChatModel):
    """Script the real agent loop without a model service."""

    def bind_tools(self, tools, **kwargs):
        return self


def call(name, args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": name}])


def replies(amount: float = 420, outcome: str = "approve", attempt_payment=True):
    messages = [
        call("read_claim", {}),
        call("read_policy_wording", {}),
        call("record_decision", decision(amount, outcome)),
    ]
    if outcome == "approve" and attempt_payment:
        messages.append(call("pay_claim", {"amount": amount}))
    messages.append(AIMessage(content="Decision recorded."))
    return messages


@pytest.fixture
def client(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / "agent.sqlite3")) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        yield client


@pytest.mark.parametrize(
    ("amount", "outcome", "pay"),
    [
        (420, "approve", True),
        (0, "decline", False),
        (0, "refer", False),
        (1250, "approve", False),
        (1250, "approve", True),
    ],
)
def test_agent_paths(client, monkeypatch, amount, outcome, pay):
    seen = []
    original = GenericFakeChatModel._generate

    def capture(model, messages, **kwargs):
        seen.extend(messages)
        return original(model, messages, **kwargs)

    monkeypatch.setattr(StubModel, "_generate", capture)
    stub = StubModel(messages=iter(replies(amount, outcome, pay)))
    assert main.run("CLM-1001", "thread-1", stub) == "Decision recorded."
    detail = client.get("/api/claims/CLM-1001").json()
    assert detail["decision"]["outcome"] == outcome
    assert bool(detail["payment"]) == (outcome == "approve" and amount <= 1000)
    combined = " ".join(str(m.content) for m in seen)
    assert "Maya Patel" in combined
    assert "## 12. Definitions" in combined
    assert "UnderCover Agent" in combined
    assert "previous_claims" in combined


def test_bound_tools_and_failures(client):
    tools = {tool.name: tool for tool in main.tools_for("CLM-1001")}
    assert set(tools) == {
        "read_claim",
        "read_policy_wording",
        "record_decision",
        "pay_claim",
    }
    assert "claim_id" not in tools["record_decision"].args
    assert tools["record_decision"].invoke(decision(0))["status_code"] == 422
    assert tools["pay_claim"].invoke({"amount": 1})["status_code"] == 409
    stub = StubModel(messages=iter([AIMessage(content="I am done.")]))
    with pytest.raises(RuntimeError, match="without a recorded decision"):
        main.run("CLM-1001", "thread-1", stub)
    unpaid = StubModel(messages=iter(replies(attempt_payment=False)))
    with pytest.raises(RuntimeError, match="still unpaid"):
        main.run("CLM-1001", "thread-2", unpaid)


def test_recursion_limit_and_no_file_access(client, monkeypatch):
    def no_files(*args, **kwargs):
        pytest.fail("Agent tried to open a file")

    instructions = client.get("/api/agent/instructions").json()
    original = main.request

    def request(method, path, body=None):
        if path == "/api/agent/instructions":
            return instructions
        return original(method, path, body)

    monkeypatch.setattr(main, "request", request)
    monkeypatch.setattr(Path, "read_text", no_files)
    monkeypatch.setattr(main, "MAX_STEPS", 4)
    stub = StubModel(messages=iter([call("read_claim", {}) for _ in range(10)]))
    with pytest.raises(GraphRecursionError):
        main.run("CLM-1001", "thread-1", stub)


def test_model_configuration(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    assert main.model().model_name == "openai/gpt-6-luna"
    assert main.model().reasoning_effort == "low"
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    assert main.model().model_name == "openrouter/free"
    assert main.model().reasoning_effort is None
