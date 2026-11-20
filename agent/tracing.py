"""Configure one Langfuse session per claim attempt, including later resumes."""

import os

from langchain_core.runnables import RunnableConfig
from langfuse.langchain import CallbackHandler


def claim_config(
    claim_id: str,
    thread_id: str,
    run_id: str | None = None,
    round_id: str | None = None,
) -> RunnableConfig:
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        config["callbacks"] = [CallbackHandler()]
        config["metadata"] = {"langfuse_session_id": f"{claim_id}/{thread_id}"}
        if run_id is not None:
            config["metadata"]["langfuse_tags"] = (
                [run_id, round_id] if round_id else [run_id]
            )
    return config


def improvement_config(round_id: str) -> RunnableConfig:
    name = f"Improvement {round_id}"
    config: RunnableConfig = {
        "configurable": {"thread_id": round_id},
        "run_name": name,
        "recursion_limit": 256,
        "metadata": {"langfuse_session_id": name},
    }
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        config["callbacks"] = [CallbackHandler()]
    return config
