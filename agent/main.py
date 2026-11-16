"""The UnderCover Agent, using backend HTTP tools only."""

import json
import os
import time
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from agent.middleware import HarnessMiddleware
from backend.schemas import DecisionInput

load_dotenv()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
MAX_STEPS = 60
CLAIM_SECONDS = 180


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


def mutation(method: str, url: str, body: object, claim_path: str) -> dict:
    try:
        return request(method, url, body)
    except httpx.TransportError:
        if method != "POST":
            raise
        detail = request("GET", claim_path)
        if url.endswith("/payment") and isinstance(body, dict):
            saved = detail.get("payment")
            if saved and float(saved["amount"]) == float(body["amount"]):
                return saved
        if url.endswith("/decision") and isinstance(body, dict):
            saved = detail.get("decision")
            if saved and all(str(saved.get(k)) == str(v) for k, v in body.items()):
                return saved
        raise


def tools_for(claim_id: str, thread_id: str = "") -> list[BaseTool]:
    """Bind all claim mutations to the requested claim."""
    path = f"/api/claims/{claim_id}"
    deadline = time.monotonic() + CLAIM_SECONDS

    def perform(name: str, method: str, url: str, body: object = None) -> dict:
        if time.monotonic() > deadline:
            raise TimeoutError("Agent exceeded its work deadline")
        emit(thread_id, "tool_started", name, f"Calling {name}")
        result = mutation(method, url, body, path)
        if name == "read_guidance_section" and "error" not in result:
            emit(thread_id, "section_read", name, json.dumps(result))
        text = str(result["error"]) if "error" in result else "Completed successfully"
        emit(thread_id, "tool_result", name, text)
        return result

    @tool
    def read_claim() -> dict:
        """Read this claim, all evidence, policy, plan, and customer history."""
        return perform("read_claim", "GET", path)

    @tool
    def list_sections(document: str) -> dict:
        """List section titles/version for document 'manual' or 'wording'."""
        return perform("list_sections", "GET", f"/api/guidance/{document}/sections")

    @tool
    def search_guidance(document: str, query: str) -> dict:
        """Find section excerpts in 'manual' or 'wording' using a search query."""
        return perform(
            "search_guidance",
            "GET",
            f"/api/guidance/{document}/search?{urlencode({'query': query})}",
        )

    @tool
    def read_guidance_section(document: str, section_id: str) -> dict:
        """Read one complete numbered section from 'manual' or 'wording'."""
        return perform(
            "read_guidance_section",
            "GET",
            f"/api/guidance/{document}/sections/{section_id}",
        )

    @tool(args_schema=DecisionInput.model_json_schema())
    def record_decision(**decision) -> dict:
        """Save the final decision, calculation and guidance section numbers."""
        return perform("record_decision", "POST", f"{path}/decision", decision)

    @tool
    def pay_claim(amount: float) -> dict:
        """Record the full approved simulated payment; sign-off is enforced."""
        return perform("pay_claim", "POST", f"{path}/payment", {"amount": amount})

    return [
        read_claim,
        list_sections,
        search_guidance,
        read_guidance_section,
        record_decision,
        pay_claim,
    ]


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
        reasoning_effort="low" if not name or name == DEFAULT_MODEL else None,
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
        system_prompt=instructions["claim"],
        middleware=[HarnessMiddleware(CLAIM_SECONDS)],
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": f"Decide claim {claim_id}."}]},
        config={"configurable": {"thread_id": thread_id}, "recursion_limit": MAX_STEPS},
    )
    verify_result(claim_id)
    return result["messages"][-1].text
