"""Fan out one run's claims to the durable claim harness."""

import logging
from collections.abc import Callable, Sequence
from operator import add
from typing import Annotated, NotRequired, TypedDict, cast

from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, Send

MAX_CONCURRENCY = 25
TRANSIENT_ERRORS = (ModelRateLimitError, ModelTimeoutError)
WORK_RETRY = RetryPolicy(max_attempts=3, retry_on=TRANSIENT_ERRORS)
log = logging.getLogger(__name__)


class ClaimWork(TypedDict):
    claim_id: str
    run_id: str
    disposition: NotRequired[str]


class DispatchState(TypedDict):
    claim_ids: list[str]
    results: Annotated[list[tuple[str, str]], add]


def dispatch(
    list_claims: Callable[[], Sequence[str]],
    work_claim: Callable[[str, str], str],
    run_id: str,
    concurrency: int,
) -> dict[str, str]:
    """Work the listed claims at the requested concurrency and return dispositions."""
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise ValueError(f"Concurrency must be between 1 and {MAX_CONCURRENCY}")

    def work(state: ClaimWork):
        return {"disposition": work_claim(state["claim_id"], state["run_id"])}

    def handle_error(state: ClaimWork, error: NodeError):
        if isinstance(error.error, TRANSIENT_ERRORS):
            return {"disposition": "in_progress"}
        log.error(
            "Dispatch worker failed for claim %s (%s)",
            state["claim_id"],
            type(error.error).__name__,
        )
        return {"disposition": "failed"}

    # LangGraph 1.2.12 re-raises handled exceptions after parallel Send tasks.
    # Apply the worker policy in an independent, single-task invocation.
    policy = StateGraph(ClaimWork)
    policy.add_node(
        "work",
        work,
        retry_policy=WORK_RETRY,
        # LangGraph injects NodeError even though its node type omits this argument.
        error_handler=cast(Callable[..., dict[str, str]], handle_error),
    )
    policy.add_edge(START, "work")
    policy.add_edge("work", END)
    worker = policy.compile(checkpointer=False)

    def load_claims(state: DispatchState):
        return {"claim_ids": list(list_claims())}

    def fan_out(state: DispatchState):
        return [
            Send("worker", ClaimWork(claim_id=claim_id, run_id=run_id))
            for claim_id in state["claim_ids"]
        ] or END

    def run_worker(state: ClaimWork):
        result = worker.invoke(state)
        return {"results": [(state["claim_id"], result["disposition"])]}

    builder = StateGraph(DispatchState)
    builder.add_node("list_claims", load_claims)
    builder.add_node("worker", run_worker)
    builder.add_edge(START, "list_claims")
    builder.add_conditional_edges("list_claims", fan_out, ["worker", END])
    builder.add_edge("worker", END)
    graph = builder.compile(checkpointer=False)
    result = graph.invoke(
        {"claim_ids": [], "results": []}, {"max_concurrency": concurrency}
    )
    return dict(result["results"])
