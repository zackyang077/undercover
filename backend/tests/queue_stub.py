"""Script the actual queue agent loop without any provider calls."""

from langchain_core.messages import AIMessage

from agent import main
from agent.queue import run_queue
from agent.tests.test_agent import StubModel, call


def scripted_queue(claim_runner, reverse=False):
    def runner(queue_run_id, thread_id):
        path = f"/api/queue/runs/{queue_run_id}"

        def messages():
            yield call("read_queue", {})
            saved = main.request("GET", "/api/queue/runs/current")
            ids = [i["claim_id"] for i in saved["items"]]
            if reverse:
                ids.reverse()
            ids = ids[: saved["target"]]
            yield call(
                "write_todos",
                {
                    "items": [
                        {"claim_id": c, "text": f"Review {c}", "status": "pending"}
                        for c in ids
                    ]
                },
            )
            for claim_id in ids:
                if main.request("GET", f"{path}/check")["stop_requested"]:
                    break
                yield call("delegate_claim", {"claim_id": claim_id})
            for claim_id in main.request("GET", f"{path}/check")["retries"]:
                if main.request("GET", f"{path}/check")["stop_requested"]:
                    break
                yield call("delegate_claim", {"claim_id": claim_id})
            yield call("check_queue", {})
            saved = main.request("GET", "/api/queue/runs/current")
            complete = sum(i["status"] == "completed" for i in saved["items"])
            failed = sum(i["status"] == "failed" for i in saved["items"])
            yield AIMessage(
                content=f"{complete} completed; {failed} technical failures."
            )

        return run_queue(
            queue_run_id, thread_id, StubModel(messages=messages()), claim_runner
        )

    return runner
