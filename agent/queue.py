"""An agent-led queue with fresh, isolated claim helpers."""

import json
from collections.abc import Callable

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from agent import main
from agent.middleware import HarnessMiddleware
from backend.queue_runs import Todos


def tools_for(
    queue_run_id: str, claim_runner: Callable[[str, str], str]
) -> list[BaseTool]:
    path = f"/api/queue/runs/{queue_run_id}"

    @tool
    def read_queue() -> dict:
        """Read this queue run's scope, target, claim summaries, and current to-dos."""
        saved = main.request("GET", "/api/queue/runs/current")
        if saved["queue_run_id"] != queue_run_id:
            raise RuntimeError("Queue run changed")
        return saved

    @tool(args_schema=Todos.model_json_schema())
    def write_todos(**request) -> dict:
        """Save the visible claim plan with statuses matching recorded outcomes."""
        return main.request("PUT", f"{path}/todos", request)

    @tool
    def check_queue() -> dict:
        """Find missed claims, pending retries, unused slots, and stop requests."""
        return main.request("GET", f"{path}/check")

    @tool
    def delegate_claim(claim_id: str) -> dict:
        """Work one claim with a fresh helper and return its verified result line."""
        attempt = main.request("POST", f"{path}/claims/{claim_id}/attempts")
        if "error" in attempt:
            return attempt
        error = ""
        try:
            claim_runner(claim_id, attempt["thread_id"])
        except Exception as failure:
            error = (
                str(failure)
                if isinstance(failure, (RuntimeError, TimeoutError))
                else f"{type(failure).__name__}: helper request failed"
            )
        return main.request(
            "POST", f"{path}/attempts/{attempt['thread_id']}/finish", {"error": error}
        )

    return [read_queue, write_todos, delegate_claim, check_queue]


def run_queue(
    queue_run_id: str,
    thread_id: str,
    llm: BaseChatModel | None = None,
    claim_runner: Callable[[str, str], str] = main.run,
) -> str:
    """Plan and work the queue, correcting an early finish once before failing."""
    instructions = main.request("GET", "/api/agent/instructions")
    saved = main.request("GET", "/api/queue/runs/current")
    target = saved["target"]
    agent = create_agent(
        llm or main.model(),
        tools=tools_for(queue_run_id, claim_runner),
        system_prompt=instructions["queue"],
        middleware=[HarnessMiddleware(120 + target * main.CLAIM_SECONDS * 2)],
    )
    messages: list[AnyMessage] = [
        HumanMessage(content=f"Work queue run {queue_run_id}.")
    ]
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 20 + 20 * target,
        "max_concurrency": 1,
    }
    for _ in range(2):
        result = agent.invoke({"messages": [*messages]}, config=config)
        check = main.request("GET", f"/api/queue/runs/{queue_run_id}/check")
        if check["complete"] or check["stop_requested"] or check["resumable"]:
            return result["messages"][-1].text
        messages = result["messages"] + [
            HumanMessage(
                content="Work is incomplete. Resolve these omissions before finishing: "
                + json.dumps(check)
            )
        ]
    raise RuntimeError(
        "Queue agent left missed claims or pending retries after correction"
    )
