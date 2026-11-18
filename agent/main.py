"""The UnderCover Agent, using backend HTTP tools only."""

import json
import os
import time
from urllib.parse import urlencode

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from agent import claim_graph
from backend.payout import PayoutInput
from backend.schemas import DecisionInput

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
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


def claim_operation(method: str, path: str, body: object = None) -> dict:
    """Read claim facts or issue a replayable claim mutation."""
    try:
        return request(method, path, body)
    except httpx.TransportError:
        return request(method, path, body)


def claim_request(thread_id: str):
    deadline = time.monotonic() + CLAIM_SECONDS

    def perform(name: str, method: str, url: str, body: object = None) -> dict:
        if time.monotonic() > deadline:
            raise TimeoutError("Agent exceeded its work deadline")
        emit(thread_id, "tool_started", name, f"Calling {name}")
        result = claim_operation(method, url, body)
        if name == "read_guidance_section" and "error" not in result:
            emit(thread_id, "section_read", name, json.dumps(result))
        text = str(result["error"]) if "error" in result else "Completed successfully"
        emit(thread_id, "tool_result", name, text)
        return result

    return perform


def tools_for(claim_id: str, thread_id: str = "") -> list[BaseTool]:
    """Bind all claim mutations to the requested claim."""
    path = f"/api/claims/{claim_id}"
    perform = claim_request(thread_id)

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

    @tool(args_schema=PayoutInput.model_json_schema())
    def work_out_payout(**inputs) -> dict:
        """Calculate a covered benefit with its eligible expenses and refunds."""
        return perform("work_out_payout", "POST", f"{path}/payout-calculation", inputs)

    return [
        read_claim,
        list_sections,
        search_guidance,
        read_guidance_section,
        work_out_payout,
        record_decision,
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


def run(claim_id: str, thread_id: str, llm: BaseChatModel | None = None) -> str:
    """Advance the claim graph on one durable thread."""
    nodes = claim_graph.ClaimGraph(
        claim_id=claim_id,
        thread_id=thread_id,
        request=claim_operation,
        emit=emit,
        tools={tool.name: tool for tool in tools_for(claim_id, thread_id)},
        get_model=lambda: llm or model(),
        deadline=time.monotonic() + CLAIM_SECONDS,
    )
    return claim_graph.run(nodes)
