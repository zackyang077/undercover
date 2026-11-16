"""The UnderCover Agent, using backend HTTP tools only."""

import os
import time

import httpx
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from backend.schemas import DecisionInput

load_dotenv()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
MAX_STEPS = 30


def backend() -> httpx.Client:
    """Return a client for the local claims API."""
    return httpx.Client(
        base_url=os.getenv("BACKEND_URL", "http://127.0.0.1:8000"),
        timeout=10,
    )


def request(method: str, path: str, body: object = None) -> dict:
    with backend() as client:
        response = client.request(method, path, json=body)
    if response.is_error:
        return {"error": response.json()["detail"], "status_code": response.status_code}
    return response.json()


def tools_for(claim_id: str, thread_id: str = "") -> list[BaseTool]:
    """Bind all claim mutations to the requested claim."""
    path = f"/api/claims/{claim_id}"
    deadline = time.monotonic() + 120

    def perform(name: str, method: str, url: str, body: object = None) -> dict:
        if time.monotonic() > deadline:
            raise TimeoutError("Agent exceeded its two-minute work limit")
        emit(thread_id, "tool_started", name, f"Calling {name}")
        result = request(method, url, body)
        text = str(result["error"]) if "error" in result else "Completed successfully"
        emit(thread_id, "tool_result", name, text)
        return result

    @tool
    def read_claim() -> dict:
        """Read this claim, all evidence, policy, plan, and customer history."""
        return perform("read_claim", "GET", path)

    @tool
    def read_policy_wording() -> dict:
        """Read the full policy wording and its version."""
        return perform("read_policy_wording", "GET", "/api/policy-wording")

    @tool(args_schema=DecisionInput.model_json_schema())
    def record_decision(**decision) -> dict:
        """Save the final decision, calculation and guidance section numbers."""
        return perform("record_decision", "POST", f"{path}/decision", decision)

    @tool
    def pay_claim(amount: float) -> dict:
        """Record the full approved simulated payment; sign-off is enforced."""
        return perform("pay_claim", "POST", f"{path}/payment", {"amount": amount})

    return [read_claim, read_policy_wording, record_decision, pay_claim]


def emit(thread_id: str, kind: str, tool_name: str, text: str):
    if thread_id:
        result = request(
            "POST",
            f"/api/runs/{thread_id}/events",
            {
                "kind": kind,
                "tool_name": tool_name,
                "text": text,
            },
        )
        if result.get("status_code") not in (None, 404):
            raise RuntimeError("Activity could not be recorded for this run")


def model() -> ChatOpenAI:
    """Return the configured OpenRouter model."""
    name = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=name or DEFAULT_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if name else "low",
        timeout=45,
        max_retries=0,
    )


def verify_result(claim_id: str):
    detail = request("GET", f"/api/claims/{claim_id}")
    decision = detail.get("decision")
    if not decision:
        raise RuntimeError("The agent finished without a recorded decision")
    payable = (
        decision["outcome"] == "approve" and detail["status"] != "waiting_sign_off"
    )
    if payable and not detail["payment"]:
        raise RuntimeError("Decision recorded, but simulated payment is still unpaid")


def run(claim_id: str, thread_id: str, llm: BaseChatModel | None = None) -> str:
    """Decide one claim using the backend and return the final summary."""
    instructions = request("GET", "/api/agent/instructions")
    agent = create_agent(
        llm or model(),
        tools=tools_for(claim_id, thread_id),
        system_prompt=instructions["content"],
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": f"Decide claim {claim_id}."}]},
        config={"configurable": {"thread_id": thread_id}, "recursion_limit": MAX_STEPS},
    )
    verify_result(claim_id)
    return result["messages"][-1].text
