"""Configure one Langfuse session per claim attempt, including later resumes."""

import os

from langchain_core.runnables import RunnableConfig
from langfuse.langchain import CallbackHandler


def claim_config(claim_id: str, thread_id: str) -> RunnableConfig:
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        config["callbacks"] = [CallbackHandler()]
        config["metadata"] = {"langfuse_session_id": f"{claim_id}/{thread_id}"}
    return config
