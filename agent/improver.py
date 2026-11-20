"""Improve one claim configuration with two human-reviewed proposal tools."""

import json
import os
from collections.abc import Callable
from contextvars import Context
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    InputAgentState,
)
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.types import Command
from pydantic import SecretStr

from agent.checkpoints import open_saver
from agent.improver_api import RoundClient
from agent.main import DEFAULT_MODEL, OPENROUTER_BASE_URL
from agent.tracing import improvement_config

GOAL = """Improve the claim agent within UnderCover's objectives and round budget.
The agent decides one travel claim from its instructions, the policy wording and
claims manual, the customer's policy, claim evidence and earlier claims. Its
tools search and read guidance sections, calculate payouts and record its decision.
Aim for changes that generalize to future claims. Never name a particular claim
in the instructions or recipe. Your proposals need a handler's approval.
Follow the recipe below for this round. The backend evaluates approved candidates
and decides whether to keep them using the held-out scores.
"""


def improver_model():
    override = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=override or DEFAULT_MODEL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        base_url=OPENROUTER_BASE_URL,
        model_kwargs={"parallel_tool_calls": False},
        reasoning_effort=None if override else "medium",
        max_retries=0,
        timeout=120,
        use_responses_api=False,
    )


class RoundUsage(AgentMiddleware):
    def __init__(self, client: RoundClient):
        self.client = client

    def wrap_model_call(self, request, handler):
        self.client.post("reserve-call", {})
        response = handler(request)
        for message in response.result:
            if isinstance(message, AIMessage):
                usage = message.response_metadata.get("token_usage") or {}
                self.client.post(
                    "usage",
                    {"call_id": message.id or str(uuid4()), "cost": usage.get("cost")},
                )
        return response


def evaluations(client: RoundClient, evaluate: Callable):
    while True:
        step = client.post("next-evaluation", {})
        if step.get("done"):
            return step
        Context().run(evaluate, client, step)


def tools_for_round(client: RoundClient, evaluate: Callable):
    @tool
    def read_results() -> str:
        """Read scores, evaluation mistakes, configuration, goals and history."""
        return json.dumps(client.get("/results"))

    @tool
    def propose_change(
        instructions: str, model: str, reasoning_effort: str, reason: str
    ) -> str:
        """Propose full instructions, a permitted model and effort, and your reason."""
        return (
            "The handler approved the proposed configuration. Evaluate the candidate."
        )

    @tool
    def run_evaluation() -> str:
        """Evaluate the approved candidate on both sets and read the verdict."""
        return json.dumps(evaluations(client, evaluate))

    @tool
    def propose_recipe(recipe: str, reason: str) -> str:
        """Propose a complete recipe for future rounds and give your reason."""
        return "The handler approved the recipe for future rounds."

    return [read_results, propose_change, run_evaluation, propose_recipe]


def review_command(choice: str, reason: str):
    decision = {"type": choice}
    if choice == "reject":
        decision["message"] = reason
    return Command(resume={"decisions": [decision]})


def review_request(result):
    interrupted = result.get("__interrupt__")
    if not interrupted:
        return None
    requests = interrupted[0].value["action_requests"]
    if len(requests) != 1:
        raise ValueError("Expected one proposal to review")
    return requests[0]


def allowed_proposal(request, models):
    if request["name"] != "propose_change":
        return True
    args = request["args"]
    return any(
        entry["model"] == args.get("model")
        and args.get("reasoning_effort") in entry["reasoning_efforts"]
        for entry in models
    )


def run_round(client: RoundClient, evaluate, decision=None, *, model=None):
    details = client.get()
    if decision is None:
        baseline = evaluations(client, evaluate)
        if baseline["status"] != "working":
            return
        inputs: InputAgentState | Command = {
            "messages": [HumanMessage("Carry out one round of your recipe.")]
        }
    else:
        inputs = review_command(decision["choice"], decision["reason"])
    approval = HumanInTheLoopMiddleware(
        interrupt_on={
            "propose_change": {"allowed_decisions": ["approve", "reject"]},
            "propose_recipe": {"allowed_decisions": ["approve", "reject"]},
        }
    )
    with open_saver() as saver:
        agent = create_agent(
            model=model or improver_model(),
            tools=tools_for_round(client, evaluate),
            middleware=[RoundUsage(client), approval],
            system_prompt=f"{GOAL}\n# Recipe\n\n{details['recipe']}",
            checkpointer=saver,
        )
        config = improvement_config(client.id)
        while True:
            result = agent.invoke(inputs, config, durability="sync")
            proposal = review_request(result)
            if not proposal:
                client.post("finish", {})
                return
            if allowed_proposal(proposal, details["models"]):
                kind = (
                    "configuration"
                    if proposal["name"] == "propose_change"
                    else "recipe"
                )
                client.post("proposal", {"kind": kind, "args": proposal["args"]})
                return
            inputs = review_command(
                "reject", "Choose a model and effort from the allowed models."
            )
