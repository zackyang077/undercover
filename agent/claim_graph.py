"""A synchronous claim graph with one checkpoint per model, tool and financial step."""

import json
import logging
import time
from collections.abc import Callable
from contextvars import Context
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic_ns
from typing import NotRequired, TypedDict, cast
from uuid import uuid4

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from agent.checkpoints import open_saver
from agent.configuration import RunConfiguration
from agent.middleware import RETRY_DELAY, transient
from agent.tracing import claim_config

MAX_MODEL_CALLS = 30
WAITING_FOR_SIGN_OFF = "Waiting for sign-off"
log = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ClaimState(TypedDict):
    claim_id: str
    thread_id: str
    messages: list[AnyMessage]
    calls: int
    retry: bool
    route: str
    amount: float
    summary: str
    model_name: NotRequired[str]
    reasoning_effort: NotRequired[str]


def recorded(detail: dict) -> dict:
    decision = detail["decision"]
    amount = decision["payout_amount"]
    if decision["outcome"] != "approve" or detail["payment"]:
        return {"route": END, "summary": f"{decision['outcome']}: ${amount:.2f}"}
    if detail["status"] == "waiting_sign_off":
        return {
            "route": "sign_off",
            "amount": amount,
            "summary": WAITING_FOR_SIGN_OFF,
        }
    return {"route": "payment", "amount": amount}


@dataclass
class ClaimGraph:
    claim_id: str
    thread_id: str
    run_id: str | None
    request: Callable
    emit: Callable
    tools: dict[str, BaseTool]
    configuration: Callable[[], RunConfiguration]
    bound_model: Callable
    deadline: float
    segment_id: str = field(default_factory=lambda: str(uuid4()), init=False)
    started_at: str = field(init=False)
    started_ns: int = field(init=False)
    last_elapsed_ms: int = field(default=-1, init=False)

    def __post_init__(self) -> None:
        self.started_at = utc_now()
        self.started_ns = monotonic_ns()

    @property
    def path(self) -> str:
        return f"/api/claims/{self.claim_id}"

    def check_time(self) -> None:
        if time.monotonic() > self.deadline:
            raise TimeoutError("Agent exceeded its work deadline")

    def event(self, step: str, text: str) -> None:
        self.check_time()
        self.emit(self.thread_id, "step", step, text)

    def record_telemetry(self, resource: str, values: dict) -> None:
        report = self.request(
            "POST",
            f"/api/runs/{self.thread_id}/{resource}",
            {"claim_id": self.claim_id, "run_id": self.run_id, **values},
        )
        if "error" in report:
            raise RuntimeError(f"Could not record claim {resource}: {report['error']}")

    def measure_work(self, *, finished: bool = False) -> None:
        elapsed_ms = (monotonic_ns() - self.started_ns) // 1_000_000
        if not finished and elapsed_ms == self.last_elapsed_ms:
            return
        self.record_telemetry(
            "work-segments",
            {
                "segment_id": self.segment_id,
                "started_at": self.started_at,
                "finished_at": utc_now() if finished else None,
                "elapsed_ms": elapsed_ms,
            },
        )
        self.last_elapsed_ms = elapsed_ms

    def start(self, state: ClaimState) -> dict:
        self.event("start", "Checking claim for a saved decision")
        detail = self.request("GET", self.path)
        if detail.get("decision"):
            return recorded(detail)
        configuration = self.configuration()
        messages = [
            SystemMessage(configuration.instructions),
            HumanMessage(f"Decide claim {self.claim_id}."),
        ]
        return {
            "route": "reserve",
            "messages": messages,
            "model_name": configuration.model,
            "reasoning_effort": configuration.reasoning_effort,
        }

    @staticmethod
    def reserve(state: ClaimState) -> dict:
        if state["calls"] >= MAX_MODEL_CALLS:
            raise RuntimeError(
                f"Claim reached its limit of {MAX_MODEL_CALLS} model calls"
            )
        return {"calls": state["calls"] + 1}

    def model(self, state: ClaimState) -> dict:
        self.event("model", f"Reviewing claim (model call {state['calls']})")
        if "model_name" in state and "reasoning_effort" in state:
            model = self.bound_model(state["model_name"], state["reasoning_effort"])
        else:
            # Checkpoints from before configuration versioning contain only messages.
            configuration = self.configuration()
            model = self.bound_model(
                configuration.model, configuration.reasoning_effort
            )
        call_id = str(uuid4())
        try:
            answer = model.invoke(state["messages"])
        except Exception as error:
            try:
                self.record_telemetry("usage", {"call_id": call_id, "usage": None})
            except Exception:
                log.exception("Could not record usage for a failed model call")
            if self.run_id is not None or not transient(error) or state["retry"]:
                raise
            time.sleep(RETRY_DELAY)
            return {"route": "reserve", "retry": True}
        usage = answer.response_metadata.get("token_usage")
        self.record_telemetry(
            "usage",
            {"call_id": call_id, "usage": usage if isinstance(usage, dict) else None},
        )
        self.measure_work()
        self.check_time()
        if not isinstance(answer, AIMessage) or not answer.tool_calls:
            raise RuntimeError("The agent finished without a recorded decision")
        calls = answer.tool_calls
        route = (
            "decision"
            if len(calls) == 1 and calls[0]["name"] == "record_decision"
            else "tools"
        )
        return {
            "messages": [*state["messages"], answer],
            "route": route,
            "retry": False,
        }

    def execute_tools(self, state: ClaimState) -> dict:
        message = cast(AIMessage, state["messages"][-1])
        responses = []
        for call in message.tool_calls:
            name = call["name"]
            if len(message.tool_calls) > 1:
                result = {"error": "Call one tool at a time."}
            elif name not in self.tools:
                result = {"error": f"Unknown claim tool: {name}"}
            else:
                try:
                    result = self.tools[name].invoke(call["args"])
                except ValidationError as error:
                    result = {"error": str(error)}

            if (
                name == "record_decision"
                and "decision" in result
                and result["decision"]
            ):
                next_step = recorded(result)
                if next_step["route"] == "sign_off":
                    self.event("waiting_sign_off", "Waiting for handler sign-off")
                # The decision is saved. Earlier investigation messages remain
                # in checkpoint history but are not needed by the payment steps.
                return {**next_step, "messages": []}
            if "error" in result:
                self.event("refusal", str(result["error"]))
            responses.append(
                ToolMessage(
                    content=json.dumps(result),
                    tool_call_id=call["id"] or name,
                    name=name,
                )
            )
        return {"messages": [*state["messages"], *responses], "route": "reserve"}

    def sign_off(self, state: ClaimState) -> dict:
        signed = interrupt({"claim_id": self.claim_id, "amount": state["amount"]})
        if not signed:
            raise RuntimeError("Handler sign-off is required")
        self.event("sign_off", "Resuming after recorded handler sign-off")
        return {"route": "payment"}

    def saved_sign_off(self) -> dict | None:
        record = self.request("GET", f"/api/work/{self.claim_id}/sign-off")
        return record["sign_off"]

    def payment(self, state: ClaimState) -> dict:
        self.check_time()
        detail = self.request(
            "POST", f"{self.path}/payment", {"amount": state["amount"]}
        )
        if not detail.get("payment"):
            raise RuntimeError(str(detail.get("error", "Payment was not recorded")))
        self.event("payment", "Recorded approved simulated payment")
        return recorded(detail)

    def compile(self, saver: BaseCheckpointSaver):
        builder = StateGraph(ClaimState)
        builder.add_node("start", self.start)
        builder.add_node("reserve", self.reserve)
        builder.add_node("model", self.model)
        builder.add_node("tools", self.execute_tools)
        builder.add_node("decision", self.execute_tools)
        builder.add_node("sign_off", self.sign_off)
        builder.add_node("payment", self.payment)
        builder.add_edge(START, "start")
        builder.add_edge("reserve", "model")
        builder.add_edge("payment", END)
        for node in ("start", "model", "tools", "decision", "sign_off"):
            builder.add_conditional_edges(node, lambda state: state["route"])
        return builder.compile(checkpointer=saver)


def work(nodes: ClaimGraph, signed_record: dict | None) -> str:
    claim_id, thread_id = nodes.claim_id, nodes.thread_id
    config = claim_config(claim_id, thread_id, nodes.run_id)
    config["recursion_limit"] = 4 * MAX_MODEL_CALLS + 12
    with open_saver() as saver:
        graph = nodes.compile(saver)
        if signed_record is not None:
            # The service observed this thread's durable interrupt locally and
            # matched it to the backend's committed sign-off before calling us.
            result = graph.invoke(
                Command(resume=signed_record), config, durability="sync"
            )
            return result["summary"]
        snapshot = graph.get_state(config)
        inputs: ClaimState | Command | None = None
        if not snapshot.values:
            inputs = ClaimState(
                claim_id=claim_id,
                thread_id=thread_id,
                messages=[],
                calls=0,
                retry=False,
                route="start",
                amount=0,
                summary="",
            )
        elif snapshot.values["claim_id"] != claim_id:
            raise RuntimeError("This thread belongs to a different claim")
        elif not snapshot.next:
            return snapshot.values["summary"]
        elif snapshot.interrupts:
            signed = nodes.saved_sign_off()
            if not signed:
                return WAITING_FOR_SIGN_OFF
            inputs = Command(resume=signed)
        elif snapshot.next == ("model",):
            # A crash may have happened after sending the reserved provider call.
            # Reserve a fresh slot before issuing a possibly repeated request.
            graph.update_state(config, {"route": "reserve"}, as_node="model")
        result = graph.invoke(inputs, config, durability="sync")
        # Sign-off may have committed before this waiting checkpoint was written.
        if result.get("__interrupt__") and (signed := nodes.saved_sign_off()):
            result = graph.invoke(Command(resume=signed), config, durability="sync")
        return result["summary"]


def run(nodes: ClaimGraph, signed_record: dict | None = None) -> str:
    """Advance one claim from its saved checkpoint."""
    nodes.measure_work()
    try:
        result = Context().run(work, nodes, signed_record)
    except Exception:
        try:
            nodes.measure_work(finished=True)
        except Exception:
            log.exception("Could not finish claim work measurement")
        raise
    nodes.measure_work(finished=True)
    return result
