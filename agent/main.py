"""The UnderCover Agent, using backend HTTP tools only."""

import os
from typing import Literal

import httpx
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

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


def tools_for(claim_id: str) -> list[BaseTool]:
    """Bind all claim mutations to the requested claim."""
    path = f"/api/claims/{claim_id}"

    @tool
    def read_claim() -> dict:
        """Read this claim, all evidence, policy, plan, and customer history."""
        return request("GET", path)

    @tool
    def read_policy_wording() -> dict:
        """Read the full policy wording and its version."""
        return request("GET", "/api/policy-wording")

    @tool
    def record_decision(
        outcome: Literal["approve", "decline", "refer"],
        payout_amount: float,
        explanation: str,
        calculation: str,
        policy_references: list[str],
    ) -> dict:
        """Save the final decision, calculation and policy section numbers."""
        return request(
            "POST",
            f"{path}/decision",
            {
                "outcome": outcome,
                "payout_amount": payout_amount,
                "explanation": explanation,
                "calculation": calculation,
                "policy_references": policy_references,
            },
        )

    @tool
    def pay_claim(amount: float) -> dict:
        """Record the full approved simulated payment; sign-off is enforced."""
        return request("POST", f"{path}/payment", {"amount": amount})

    return [read_claim, read_policy_wording, record_decision, pay_claim]


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
        tools=tools_for(claim_id),
        system_prompt=instructions["content"],
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": f"Decide claim {claim_id}."}]},
        config={"configurable": {"thread_id": thread_id}, "recursion_limit": MAX_STEPS},
    )
    verify_result(claim_id)
    return result["messages"][-1].text
