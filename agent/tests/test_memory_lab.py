import ast
import asyncio
import importlib.util
import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

NOTEBOOK = Path(__file__).resolve().parents[2] / "labs" / "week2day2.ipynb"
FUNCTIONS = {
    "model_cost",
    "decide",
    "is_right",
    "mistake_cost",
    "found_fact",
    "kappa",
    "score_table",
    "run_scores",
    "score_run",
    "average_scores",
}


@pytest.fixture
def lab(tmp_path):
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    definitions = []
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            tree = ast.parse("".join(cell["source"]))
            definitions.extend(
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name in FUNCTIONS
            )
    assert {node.name for node in definitions} == FUNCTIONS
    source = ast.unparse(ast.Module(body=definitions, type_ignores=[]))
    module_path = tmp_path / "memory_lab.py"
    module_path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("memory_lab", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__dict__.update(
        pd=pd,
        Evaluation=SimpleNamespace,
        DECISIONS=["approve", "decline", "refer"],
        WRONGFUL_DECLINE=1500,
        NEEDLESS_REFERRAL=50,
        recorded={},
        langfuse_handler=object(),
        make_user_prompt=json.dumps,
    )
    spec.loader.exec_module(module)
    return module


def result_item(claim_id, decision, cost):
    payout = 25 if decision == "approve" else 0
    return SimpleNamespace(
        item=SimpleNamespace(
            id=claim_id,
            expected_output={"decision": decision, "payout": payout, "needs": []},
        ),
        output={
            "decision": decision,
            "payout": payout,
            "rationale": "Synthetic policy decision.",
            "retrieved": "",
            "cost": cost,
        },
    )


@pytest.mark.parametrize("costs", [[0.01, 0.02], [0.0, 0.0]])
def test_reported_costs_include_every_ai_call_and_preserve_real_zero(lab, costs):
    messages: list[BaseMessage] = [HumanMessage(content="Synthetic claim")]
    messages.extend(
        AIMessage(content="Decision", response_metadata={"token_usage": {"cost": cost}})
        for cost in costs
    )
    messages.append(ToolMessage(content="Recorded", tool_call_id="test-call"))
    assert lab.model_cost(messages) == pytest.approx(sum(costs))


@pytest.mark.parametrize(
    "metadata",
    [{}, {"token_usage": None}, {"token_usage": {}}, {"token_usage": {"cost": None}}],
)
def test_missing_cost_is_unknown_even_when_token_counts_are_available(lab, metadata):
    message = AIMessage(
        content="Decision",
        response_metadata=metadata,
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    assert lab.model_cost([message]) is None


def test_partial_cost_and_no_model_messages_are_not_reported_as_total_cost(lab):
    known = AIMessage(content="", response_metadata={"token_usage": {"cost": 0.02}})
    unknown = AIMessage(content="Decision", response_metadata={"id": "resp-test"})
    assert lab.model_cost([known, unknown]) is None
    assert lab.model_cost([HumanMessage(content="Synthetic claim")]) is None


@pytest.mark.parametrize("cost", [None, 0.0, 0.03])
def test_decision_and_retrieval_survive_missing_billing_metadata(lab, cost):
    answer = {"decision": "approve", "payout": 25, "rationale": "Synthetic decision."}
    lab.recorded["TEST-1"] = answer
    metadata = {"id": "resp-test"} if cost is None else {"token_usage": {"cost": cost}}
    agent = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "messages": [
                    AIMessage(content="Decision", response_metadata=metadata),
                    ToolMessage(
                        content="Synthetic retrieved fact",
                        name="qdrant-find",
                        tool_call_id="test-search",
                    ),
                ]
            }
        )
    )
    output = asyncio.run(lab.decide(agent, {"claim_id": "TEST-1"}))
    assert output == {**answer, "cost": cost, "retrieved": "Synthetic retrieved fact"}
    agent.ainvoke.assert_awaited_once()


def test_no_successful_results_has_a_schema_and_no_fabricated_scores(lab):
    table = lab.score_table([])
    assert table.empty
    assert set(table.columns) == {
        "claim_id",
        "handler",
        "agent",
        "handler_payout",
        "agent_payout",
        "right",
        "mistake_cost",
        "found_fact",
        "llm_cost",
        "rationale",
    }
    assert lab.run_scores(table) == {}
    assert lab.score_run(item_results=[]) == []
    assert lab.average_scores(table) == {}


@pytest.mark.parametrize("costs", [[0.01, None], [None, None], [0.0, 0.0]])
def test_run_scoring_keeps_unknown_cost_and_excludes_undefined_metrics(lab, costs):
    items = [
        result_item("TEST-2", "approve", costs[0]),
        result_item("TEST-1", "decline", costs[1]),
    ]
    table = lab.score_table(items)
    assert list(table["claim_id"]) == ["TEST-1", "TEST-2"]
    scores = lab.run_scores(table)
    assert scores["accuracy"] == 1
    unknown_cost = any(cost is None for cost in costs)
    if unknown_cost:
        assert pd.isna(scores["llm_cost"])
    else:
        assert scores["llm_cost"] == 0
    evaluations = {
        score.name: score.value for score in lab.score_run(item_results=items)
    }
    assert evaluations["accuracy"] == 1
    assert "found_fact" not in evaluations
    assert ("llm_cost" in evaluations) is not unknown_cost
    assert all(pd.notna(value) for value in evaluations.values())


def test_average_cost_stays_unknown_when_one_run_lacks_cost(lab):
    known = lab.score_table(
        [result_item("TEST-1", "approve", 0.01), result_item("TEST-2", "decline", 0.02)]
    ).assign(run=1)
    unknown = lab.score_table(
        [result_item("TEST-1", "approve", None), result_item("TEST-2", "decline", None)]
    ).assign(run=2)
    scores = lab.average_scores(pd.concat([known, unknown], ignore_index=True))
    assert scores["accuracy"] == 1
    assert pd.isna(scores["llm_cost"])


def test_failed_experiments_report_failures_and_skip_empty_charts(
    lab, tmp_path, capsys
):
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    cell_ids = {
        "358c0e50",
        "b98878f0",
        "baf32b60",
        "9ab160fd",
        "11a0e5e4",
        "ffef4393",
        "35eb63ef",
    }
    source = "\n\n".join(
        "".join(cell["source"]) for cell in notebook["cells"] if cell["id"] in cell_ids
    )
    script = tmp_path / "experiment_display.py"
    script.write_text(source, encoding="utf-8")
    experiment = Mock(
        return_value=SimpleNamespace(
            item_results=[], format=lambda: "No experiment results to display."
        )
    )
    plot = Mock()
    display = Mock()
    state = runpy.run_path(
        str(script),
        init_globals={
            **lab.__dict__,
            "N_TIMES": 1,
            "items": [object(), object()],
            "langfuse": SimpleNamespace(run_experiment=experiment),
            "payout_task": Mock(),
            "memory_task": Mock(),
            "score_claim": Mock(),
            "show_confusion_matrix": plot,
            "display": display,
        },
    )
    assert state["payout_table"].empty
    assert state["memory_table"].empty
    assert experiment.call_count == 2
    plot.assert_not_called()
    display.assert_not_called()
    output = capsys.readouterr().out
    assert output.count("Successful: 0/2; failed: 2") == 2
    assert "Both experiments need successful results" in output
