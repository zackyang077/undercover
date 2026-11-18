import json
from contextlib import nullcontext
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage

from agent import claim_graph, main, tracing
from backend.main import create_app
from backend.tests.test_finance import decision


class StubModel(GenericFakeChatModel):
    """Script the real agent loop without a model service."""

    def bind_tools(self, tools, **kwargs):
        return self


def call(name, args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": name}])


def replies(amount: float = 420, outcome: str = "approve"):
    return [
        call("read_claim", {}),
        call("read_guidance_section", {"document": "wording", "section_id": "3"}),
        call("read_guidance_section", {"document": "manual", "section_id": "4"}),
        call("record_decision", decision(amount, outcome)),
    ]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "agent.sqlite3"))
    with TestClient(create_app(tmp_path / "agent.sqlite3")) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        yield client


@pytest.mark.parametrize(
    ("amount", "outcome"),
    [
        (420, "approve"),
        (0, "decline"),
        (0, "refer"),
        (1250, "approve"),
    ],
)
def test_agent_paths(client, monkeypatch, amount, outcome):
    seen = []
    original = GenericFakeChatModel._generate

    def capture(model, messages, **kwargs):
        seen.extend(messages)
        return original(model, messages, **kwargs)

    monkeypatch.setattr(StubModel, "_generate", capture)
    stub = StubModel(messages=iter(replies(amount, outcome)))
    result = main.run("CLM-1001", "thread-1", stub)
    assert result == (
        "Waiting for sign-off" if amount > 1000 else f"{outcome}: ${amount:.2f}"
    )
    detail = client.get("/api/claims/CLM-1001").json()
    assert detail["decision"]["outcome"] == outcome
    assert bool(detail["payment"]) == (outcome == "approve" and amount <= 1000)
    combined = " ".join(str(m.content) for m in seen)
    assert "Maya Patel" in combined
    assert "Trip Delay" in combined
    assert "Working a claim" in combined
    assert "UnderCover claim helper" in combined
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
    }
    assert "claim_id" not in tools["record_decision"].args
    assert "claim_id" not in tools["work_out_payout"].args
    assert tools["record_decision"].invoke(decision(0))["status_code"] == 422
    stub = StubModel(messages=iter([AIMessage(content="I am done.")]))
    with pytest.raises(RuntimeError, match="without a recorded decision"):
        main.run("CLM-1001", "thread-1", stub)
    main.run("CLM-1001", "thread-2", StubModel(messages=iter(replies())))
    assert client.get("/api/claims/CLM-1001").json()["payment"]["amount"] == 420


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
    stub = StubModel(messages=iter(replies(1250)))
    main.run("CLM-1001", "first", stub)
    client.post(
        "/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"}
    ).raise_for_status()
    main.run("CLM-1001", "first", stub)
    main.run("CLM-1001", "next", stub)
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
    monkeypatch.setattr(claim_graph, "MAX_MODEL_CALLS", 4)
    stub = StubModel(messages=iter([call("read_claim", {}) for _ in range(10)]))
    with pytest.raises(RuntimeError, match="limit of 4 model calls"):
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
    main.run("CLM-1001", "synthetic", StubModel(messages=iter(messages)))
    saved = client.get("/api/claims/CLM-1001").json()["decision"]
    assert saved["manual_references"] == [section]
    assert saved["outcome"] == outcome
    assert saved["payout_amount"] == amount


def test_signed_claim_resumes_once_with_same_thread(client):
    thread = client.app.state.runs.start("CLM-1001").thread_id
    actor = StubModel(messages=iter(replies(1250)))
    assert main.run("CLM-1001", thread, actor) == "Waiting for sign-off"
    assert main.run("CLM-1001", thread, actor) == "Waiting for sign-off"
    claim = "/api/claims/CLM-1001"
    assert client.get(claim).json()["payment"] is None
    client.post(f"{claim}/sign-off", json={"handler_id": "HND-01"}).raise_for_status()
    no_more_model_calls = StubModel(messages=iter([]))
    assert main.run("CLM-1001", thread, no_more_model_calls) == "approve: $1250.00"
    paid = client.get(claim).json()["payment"]
    main.run("CLM-1001", thread, no_more_model_calls)
    assert client.get(claim).json()["payment"] == paid
    assert len(client.app.state.runs.list("CLM-1001")) == 1


@pytest.mark.parametrize("step", ["decision", "payment"])
def test_crash_after_mutation_replays_checkpoint(client, monkeypatch, step):
    thread = client.app.state.runs.start("CLM-1001").thread_id
    path = "/api/claims/CLM-1001"
    original = main.request

    def crash(method, url, body=None):
        result = original(method, url, body)
        if method == "POST" and url == f"{path}/{step}":
            raise SystemExit("Worker was killed")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(main, "request", crash)
        with pytest.raises(SystemExit, match="Worker was killed"):
            main.run("CLM-1001", thread, StubModel(messages=iter(replies())))
    before = client.get(path).json()
    assert before[step]
    assert (
        main.run("CLM-1001", thread, StubModel(messages=iter([]))) == "approve: $420.00"
    )
    after = client.get(path).json()
    assert after[step] == before[step]
    assert after["payment"]["amount"] == 420
    assert len(client.app.state.runs.list("CLM-1001")) == 1


def test_existing_approval_can_resume_without_a_checkpoint(client):
    path = "/api/claims/CLM-1001"
    client.post(f"{path}/decision", json=decision(1500)).raise_for_status()
    main.run("CLM-1001", "historical", StubModel(messages=iter([])))
    assert client.get(path).json()["payment"] is None
    client.post(f"{path}/sign-off", json={"handler_id": "HND-01"}).raise_for_status()
    main.run("CLM-1001", "historical", StubModel(messages=iter([])))
    assert client.get(path).json()["payment"]["amount"] == 1500


def test_graph_replays_payment_after_lost_http_response(client, monkeypatch):
    path = "/api/claims/CLM-1001"
    request = main.request
    payments = []

    def lose_first_response(method, url, body=None):
        result = request(method, url, body)
        if method == "POST" and url == f"{path}/payment":
            payments.append(result["payment"])
            if len(payments) == 1:
                raise httpx.ReadTimeout("Response lost after payment commit")
        return result

    monkeypatch.setattr(main, "request", lose_first_response)
    main.run("CLM-1001", "lost-response", StubModel(messages=iter(replies())))
    saved = client.get(path).json()["payment"]
    assert payments == [saved, saved]


def test_transient_retry_counts_against_budget(client, monkeypatch):
    calls = []
    generate = StubModel._generate

    def flaky(model, messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            raise httpx.ConnectError("Temporary failure")
        return generate(model, messages, **kwargs)

    monkeypatch.setattr(StubModel, "_generate", flaky)
    monkeypatch.setattr(claim_graph, "RETRY_DELAY", 0)
    stub = StubModel(messages=iter(call("read_claim", {}) for _ in range(31)))
    with pytest.raises(RuntimeError, match="limit of 30 model calls"):
        main.run("CLM-1001", "capped", stub)
    assert len(calls) == 30
    assert client.get("/api/claims/CLM-1001").json()["decision"] is None


def test_tool_refusal_returns_to_model(client, monkeypatch):
    refused = []
    generate = StubModel._generate

    def inspect(model, messages, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            refused.append(json.loads(messages[-1].text))
        return generate(model, messages, **kwargs)

    monkeypatch.setattr(StubModel, "_generate", inspect)
    sequence = [
        call("pay_claim", {"amount": 42}),
        call("record_decision", decision(0)),
        call("record_decision", decision(42)),
    ]
    main.run("CLM-1001", "refusals", StubModel(messages=iter(sequence)))
    assert "Unknown claim tool" in refused[0]["error"]
    assert refused[1]["status_code"] == 422
    assert client.get("/api/claims/CLM-1001").json()["payment"]["amount"] == 42


def test_signoff_committed_while_decision_is_returning(client, monkeypatch):
    path = "/api/claims/CLM-1001"
    request = main.request

    def fast_handler(method, url, body=None):
        result = request(method, url, body)
        if url == f"{path}/decision" and method == "POST":
            # The decision response still reports waiting while sign-off commits.
            assert result["status"] == "waiting_sign_off"
            request("POST", f"{path}/sign-off", {"handler_id": "HND-01"})
        return result

    monkeypatch.setattr(main, "request", fast_handler)
    result = main.run("CLM-1001", "race", StubModel(messages=iter(replies(1500))))
    assert result == "approve: $1500.00"
    assert client.get(path).json()["payment"]["amount"] == 1500


def test_restart_keeps_investigation_and_model_call_budget(client, monkeypatch):
    claim = "CLM-1001"
    thread = client.app.state.runs.start(claim).thread_id
    with monkeypatch.context() as patch:
        generate = StubModel._generate

        def stop_on_second_call(model, messages, **kwargs):
            if isinstance(messages[-1], ToolMessage):
                raise SystemExit("Process stopped during model call")
            return generate(model, messages, **kwargs)

        patch.setattr(StubModel, "_generate", stop_on_second_call)
        with pytest.raises(SystemExit, match="Process stopped"):
            main.run(claim, thread, StubModel(messages=iter([call("read_claim", {})])))

    result = main.run(
        claim, thread, StubModel(messages=iter([call("record_decision", decision())]))
    )
    assert result == "approve: $420.00"
    (run,) = client.get(f"/api/claims/{claim}/runs").json()
    assert [
        event["tool_name"] for event in run["events"] if event["kind"] == "tool_started"
    ] == ["read_claim", "record_decision"]
    assert [
        event["text"] for event in run["events"] if event["tool_name"] == "model"
    ] == [
        "Reviewing claim (model call 1)",
        "Reviewing claim (model call 2)",
        "Reviewing claim (model call 3)",
    ]
