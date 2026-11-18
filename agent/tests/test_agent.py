import json
from contextlib import nullcontext
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from agent import main, tracing
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
        call("read_guidance_section", {"document": "wording", "section_id": "3"}),
        call("read_guidance_section", {"document": "manual", "section_id": "4"}),
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
    assert "Trip Delay" in combined
    assert "Working a claim" in combined
    assert "UnderCover Agent" in combined
    assert "previous_claims" in combined


def test_bound_tools_and_failures(client):
    tools = {tool.name: tool for tool in main.tools_for("CLM-1001")}
    assert set(tools) == {
        "read_claim",
        "list_sections",
        "search_guidance",
        "read_guidance_section",
        "work_out_payout",
        "record_decision",
        "pay_claim",
    }
    assert "claim_id" not in tools["record_decision"].args
    assert "claim_id" not in tools["work_out_payout"].args
    assert tools["record_decision"].invoke(decision(0))["status_code"] == 422
    assert tools["pay_claim"].invoke({"amount": 1})["status_code"] == 409
    stub = StubModel(messages=iter([AIMessage(content="I am done.")]))
    with pytest.raises(RuntimeError, match="without a recorded decision"):
        main.run("CLM-1001", "thread-1", stub)
    unpaid = StubModel(messages=iter(replies(attempt_payment=False)))
    with pytest.raises(RuntimeError, match="still unpaid"):
        main.run("CLM-1001", "thread-2", unpaid)


def test_model_uses_payout_result(client, monkeypatch):
    captured = []
    generate = StubModel._generate

    def watch(model, messages, **kwargs):
        captured.extend(
            json.loads(msg.text)
            for msg in messages
            if isinstance(msg, ToolMessage) and msg.name == "work_out_payout"
        )
        return generate(model, messages, **kwargs)

    monkeypatch.setattr(StubModel, "_generate", watch)
    line = dict(
        description="Hotel", amount=500, quantity=1, kind="expense", nights=0, hours=0
    )
    payload = dict(benefit="trip_delay", lines=[line], paid_by_others=80)
    saved = {**decision(), "calculation": "Covered $500 less $80 refunded = $420."}
    messages = [
        call("work_out_payout", payload),
        call("record_decision", saved),
        call("pay_claim", {"amount": 420}),
        AIMessage(content="Paid."),
    ]
    main.run("CLM-1001", "calculation", StubModel(messages=iter(messages)))
    assert captured[0]["payout"] == 420
    record = client.get("/api/claims/CLM-1001").json()["decision"]
    assert all(record[key] == expected for key, expected in saved.items())


def test_claim_calls_trace_into_attempt_sessions(client, monkeypatch):
    sessions = []

    class Recorder(BaseCallbackHandler):
        def on_chain_start(self, serialized, inputs, **kwargs):
            if kwargs.get("parent_run_id") is None:
                sessions.append(kwargs["metadata"]["langfuse_session_id"])

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "test-public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "test-secret")
    monkeypatch.setattr(tracing, "CallbackHandler", Recorder)
    for thread in ["first", "first", "next"]:
        stub = StubModel(messages=iter([AIMessage(content="Done.")]))
        with pytest.raises(RuntimeError, match="without a recorded decision"):
            main.run("CLM-1001", thread, stub)
    assert sessions == ["CLM-1001/first", "CLM-1001/first", "CLM-1001/next"]
    monkeypatch.delenv("LANGFUSE_SECRET_KEY")
    assert "callbacks" not in tracing.claim_config("CLM-1001", "next")


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


@pytest.mark.parametrize("model_name", ["openrouter/free", "x-ai/grok-4.7"])
def test_model_configuration(monkeypatch, model_name):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    assert main.model().model_name == "deepseek/deepseek-v4.1-flash"
    assert main.model().reasoning_effort == "low"
    monkeypatch.setenv("MODEL_NAME", model_name)
    assert main.model().model_name == model_name
    assert main.model().reasoning_effort is None


def test_section_tools_and_activity(client):
    # Exercise an isolated registered attempt without launching the normal runner.
    thread = client.app.state.runs.start("CLM-1002").thread_id
    tools = {t.name: t for t in main.tools_for("CLM-1002", thread)}
    assert tools["list_sections"].invoke({"document": "manual"})["version"] == "2026.2"
    assert tools["search_guidance"].invoke(
        {"document": "manual", "query": "wild animals"}
    )
    result = tools["read_guidance_section"].invoke(
        {"document": "manual", "section_id": "7"}
    )
    assert "Wild animals" in result["content"]
    tools["read_guidance_section"].invoke(
        {"document": "manual", "section_id": "missing"}
    )
    events = client.get("/api/claims/CLM-1002/runs").json()[0]["events"]
    reads = [e for e in events if e["kind"] == "section_read"]
    assert len(reads) == 1
    assert '"version": "2026.2"' in reads[0]["text"]
    client.app.state.runs.finish(thread, "completed", "")


def test_lost_payment_response_reconciles(client, monkeypatch):
    client.post("/api/claims/CLM-1001/decision", json=decision())
    original = main.request

    def lost(method, path, body=None):
        saved = original(method, path, body)
        if method == "POST" and path.endswith("/payment"):
            raise httpx.ReadTimeout("Response lost")
        return saved

    monkeypatch.setattr(main, "request", lost)
    tools = {t.name: t for t in main.tools_for("CLM-1001")}
    assert tools["pay_claim"].invoke({"amount": 420})["amount_cents"] == 42000
    assert client.get("/api/claims/CLM-1001").json()["payment"]["amount"] == 420


@pytest.mark.parametrize(
    ("section", "story", "amount", "outcome"),
    [
        ("9", "This is the third claim filed within twelve months.", 0, "refer"),
        ("7", "A licensed guide confirms a wild monkey damaged a bag.", 100, "approve"),
        (
            "10",
            "One traveler has eleven hours of covered delay without meal receipts.",
            35,
            "approve",
        ),
        ("5", "The required carrier report has not been supplied.", 0, "refer"),
    ],
)
def test_synthetic_manual_tool_flows(client, section, story, amount, outcome):
    store = client.app.state.store
    claim = store.get("claims", "CLM-1001")
    claim.update(story=story, documents=[], items=[])
    with store.connection() as connection:
        store.update(connection, "claims", "CLM-1001", claim)
    payload = {**decision(amount, outcome), "manual_references": [section]}
    messages = [
        call("read_claim", {}),
        call("read_guidance_section", {"document": "wording", "section_id": "3"}),
        call("read_guidance_section", {"document": "manual", "section_id": section}),
        call("record_decision", payload),
    ]
    if amount:
        messages.append(call("pay_claim", {"amount": amount}))
    messages.append(AIMessage(content="Synthetic case recorded."))
    main.run("CLM-1001", "synthetic", StubModel(messages=iter(messages)))
    saved = client.get("/api/claims/CLM-1001").json()["decision"]
    assert saved["manual_references"] == [section]
    assert saved["outcome"] == outcome
    assert saved["payout_amount"] == amount


def test_lost_decision_response_reconciles(client, monkeypatch):
    original = main.request

    def lost(method, path, body=None):
        saved = original(method, path, body)
        if method == "POST" and path.endswith("/decision"):
            raise httpx.ReadTimeout("Response lost after saving")
        return saved

    monkeypatch.setattr(main, "request", lost)
    tools = {t.name: t for t in main.tools_for("CLM-1001")}
    assert tools["record_decision"].invoke(decision())["payout_amount"] == 420
    assert (
        client.get("/api/claims/CLM-1001").json()["decision"]["payout_cents"] == 42000
    )
