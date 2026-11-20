"""Exercise real durable improvement interrupts with a scripted chat model."""

from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage

from agent import improver, tracing
from agent.improver_api import RoundClient
from agent.round_evaluation import RoundEvaluation
from agent.tests.test_agent import StubModel


class RoundFixture(RoundClient):
    def __init__(self):
        super().__init__(str(uuid4()))
        self.writes = []
        self.pending = []
        self.steps = []
        self.status = "working"

    def get(self, resource=""):
        if resource == "/results":
            return {"accuracy": 0.72}
        return {
            "status": self.status,
            "recipe": "Follow your recipe.",
            "models": [{"model": "permitted", "reasoning_efforts": ["low"]}],
        }

    def post(self, resource, body):
        self.writes.append((resource, body))
        if resource == "next-evaluation":
            return (
                self.steps.pop(0)
                if self.steps
                else {"done": True, "status": self.status}
            )
        if resource == "proposal":
            self.pending.append(body)
        return {}


def tool_call(name, args):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": str(uuid4())}],
        response_metadata={"token_usage": {"cost": 0.00042}},
    )


def change(model):
    return tool_call(
        "propose_change",
        {
            "model": model,
            "instructions": "Use policy evidence.",
            "reasoning_effort": "low",
            "reason": "Better evidence.",
        },
    )


def test_improver_rejects_invalid_model_then_pauses_and_resumes_real_tools(monkeypatch):
    round_client = RoundFixture()
    seen = []
    generate = StubModel._generate

    def capture(model, messages, **kwargs):
        seen.extend(
            str(msg.content) for msg in messages if isinstance(msg, ToolMessage)
        )
        return generate(model, messages, **kwargs)

    monkeypatch.setattr(StubModel, "_generate", capture)
    model = StubModel(
        messages=iter(
            [
                tool_call("read_results", {}),
                change("unlisted"),
                change("permitted"),
                change("permitted"),
                tool_call("run_evaluation", {}),
                tool_call(
                    "propose_recipe",
                    {"recipe": "Use a general rule.", "reason": "Generality"},
                ),
                AIMessage(content="Finished"),
            ]
        )
    )
    ran = []

    def evaluate(_client, step):
        ran.append(step["run_id"])

    improver.run_round(round_client, evaluate, model=model)
    assert len(round_client.pending) == 1
    assert round_client.pending[0]["args"]["model"] == "permitted"
    assert ran == []
    improver.run_round(
        round_client,
        evaluate,
        {"choice": "reject", "reason": "Use comparable figures."},
        model=model,
    )
    assert any("Use comparable figures." in message for message in seen)
    assert len(round_client.pending) == 2
    round_client.steps = [{"run_id": str(index)} for index in range(3)]
    improver.run_round(
        round_client, evaluate, {"choice": "approve", "reason": ""}, model=model
    )
    assert ran == ["0", "1", "2"]
    assert round_client.pending[-1]["kind"] == "recipe"
    improver.run_round(
        round_client, evaluate, {"choice": "approve", "reason": ""}, model=model
    )
    assert round_client.writes[-1] == ("finish", {})
    assert [entry for entry in round_client.writes if entry[0] == "reserve-call"] == [
        ("reserve-call", {})
    ] * 7
    costs = [data["cost"] for action, data in round_client.writes if action == "usage"]
    assert costs == [0.00042] * 6 + [None]


def test_round_no_proposal_finishes_and_no_more_claims_after_budget(monkeypatch):
    client = RoundFixture()
    improver.run_round(
        client,
        lambda *_: None,
        model=StubModel(messages=iter([AIMessage(content="No change")])),
    )
    assert client.pending == []
    assert client.writes[-1] == ("finish", {})
    gates = iter([True, False])
    monkeypatch.setattr(
        client, "get", lambda path: {"allowed": next(gates), "reason": "Budget spent"}
    )
    control = RoundEvaluation(client, "run", cold_start=True)
    executed = []

    def work(claim, run_id):
        executed.append(claim)
        return "decided"

    def dispatcher(list_claims, guarded, run_id, _concurrency):
        return {claim: guarded(claim, run_id) for claim in list_claims()}

    result = control.dispatch(dispatcher, ["a", "b", "c"], work, 3)
    assert result == {"a": "decided", "b": "skipped", "c": "skipped"}
    assert executed == ["a"]
    assert control.reason == "Budget spent"


def test_improvement_stretches_share_session_and_claims_include_both_tags(monkeypatch):

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "test-public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "test-secret")
    monkeypatch.setattr(tracing, "CallbackHandler", object)
    first = tracing.improvement_config("round-id")
    second = tracing.improvement_config("round-id")
    assert (
        first.get("metadata")
        == second.get("metadata")
        == {"langfuse_session_id": "Improvement round-id"}
    )
    run = tracing.claim_config("claim-id", "thread-id", "run-id", "round-id")
    assert run.get("metadata") == {
        "langfuse_session_id": "claim-id/thread-id",
        "langfuse_tags": ["run-id", "round-id"],
    }
