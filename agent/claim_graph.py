"""A synchronous claim graph with one checkpoint per model, tool and financial step."""

import json
import time
from collections.abc import Callable
from contextvars import Context
from dataclasses import dataclass
from functools import cached_property
from typing import TypedDict, cast

from langchain_core.language_models import BaseChatModel
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
from agent.middleware import RETRY_DELAY, transient
from agent.tracing import claim_config

MAX_MODEL_CALLS = 30


class ClaimState(TypedDict):
    claim_id: str
    thread_id: str
    messages: list[AnyMessage]
    calls: int
    retry: bool
    route: str
    amount: float
    summary: str


def recorded(detail: dict) -> dict:
    decision = detail["decision"]
    amount = decision["payout_amount"]
    if decision["outcome"] != "approve" or detail["payment"]:
        return {"route": END, "summary": f"{decision['outcome']}: ${amount:.2f}"}
    if detail["status"] == "waiting_sign_off":
        return {
            "route": "sign_off",
            "amount": amount,
            "summary": "Waiting for sign-off",
        }
    return {"route": "payment", "amount": amount}


@dataclass
class ClaimGraph:
    claim_id: str
    thread_id: str
    request: Callable
    emit: Callable
    tools: dict[str, BaseTool]
    get_model: Callable[[], BaseChatModel]
    deadline: float

    @property
    def path(self) -> str:
        return f"/api/claims/{self.claim_id}"

    @cached_property
    def bound_model(self):
        return self.get_model().bind_tools(list(self.tools.values()))

    def check_time(self) -> None:
        if time.monotonic() > self.deadline:
            raise TimeoutError("Agent exceeded its work deadline")

    def event(self, step: str, text: str) -> None:
        self.check_time()
        self.emit(self.thread_id, "step", step, text)

    def start(self, state: ClaimState) -> dict:
        self.event("start", "Checking claim for a saved decision")
        detail = self.request("GET", self.path)
        if detail.get("decision"):
            return recorded(detail)
        instructions = self.request("GET", "/api/agent/instructions")["claim"]
        messages = [
            SystemMessage(instructions),
            HumanMessage(f"Decide claim {self.claim_id}."),
        ]
        return {"route": "reserve", "messages": messages}

    @staticmethod
    def reserve(state: ClaimState) -> dict:
        if state["calls"] >= MAX_MODEL_CALLS:
            raise RuntimeError(
                f"Claim reached its limit of {MAX_MODEL_CALLS} model calls"
            )
        return {"calls": state["calls"] + 1}

    def model(self, state: ClaimState) -> dict:
        self.event("model", f"Reviewing claim (model call {state['calls']})")
        try:
            answer = self.bound_model.invoke(state["messages"])
        except Exception as error:
            if not transient(error) or state["retry"]:
                raise
            time.sleep(RETRY_DELAY)
            return {"route": "reserve", "retry": True}
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
                return next_step
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
        interrupt({"claim_id": self.claim_id, "amount": state["amount"]})
        detail = self.request("GET", self.path)
        if not detail["sign_off"]:
            raise RuntimeError("Handler sign-off is required")
        self.event("sign_off", "Resuming after recorded handler sign-off")
        return recorded(detail)

    def payment(self, state: ClaimState) -> dict:
        self.event("payment", "Recording approved simulated payment")
        detail = self.request(
            "POST", f"{self.path}/payment", {"amount": state["amount"]}
        )
        if not detail.get("payment"):
            raise RuntimeError(str(detail.get("error", "Payment was not recorded")))
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


def work(nodes: ClaimGraph) -> str:
    claim_id, thread_id = nodes.claim_id, nodes.thread_id
    config = claim_config(claim_id, thread_id)
    config["recursion_limit"] = 4 * MAX_MODEL_CALLS + 12
    with open_saver() as saver:
        graph = nodes.compile(saver)
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
            detail = nodes.request("GET", nodes.path)
            if not detail["sign_off"]:
                return "Waiting for sign-off"
            inputs = Command(resume=detail["sign_off"])
        elif snapshot.next == ("model",):
            # A crash may have happened after sending the reserved provider call.
            # Reserve a fresh slot before issuing a possibly repeated request.
            graph.update_state(config, {"route": "reserve"}, as_node="model")
        result = graph.invoke(inputs, config, durability="sync")
        if result.get("__interrupt__"):
            # Sign-off may have committed before this waiting checkpoint was written.
            detail = nodes.request("GET", nodes.path)
            if detail["sign_off"]:
                result = graph.invoke(
                    Command(resume=detail["sign_off"]), config, durability="sync"
                )
        return result["summary"]


def run(nodes: ClaimGraph) -> str:
    """Advance one claim from its saved checkpoint."""
    return Context().run(work, nodes)
