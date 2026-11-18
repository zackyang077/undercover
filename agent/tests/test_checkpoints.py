from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint

from agent.checkpoints import open_saver


def test_thread_survives_reopening_local_checkpoint_file():
    config: RunnableConfig = {
        "configurable": {"thread_id": "attempt-1", "checkpoint_ns": ""}
    }
    checkpoint = empty_checkpoint()
    with open_saver() as saver:
        saver.put(config, checkpoint, {"step": 3}, {})
    with open_saver() as reopened:
        recovered = reopened.get_tuple(config)
        assert recovered
        assert recovered.checkpoint["id"] == checkpoint["id"]
        assert recovered.metadata.get("step") == 3
