"""Drive an improvement round across both services and their actual storage."""

from contextlib import nullcontext
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy import select

from agent import improver, improver_api, main, service
from agent.tests.test_agent import StubModel, call
from agent.tests.test_improver import tool_call
from backend.eval_sources import handler_references
from backend.judge import Grade, Judge
from backend.main import create_app
from backend.tests.handler_session import SignedClient
from backend.tests.test_finance import decision


def choose_one_claim_per_set(store):
    with store.connection(write=True) as conn:
        # One reference per set keeps the full three-trial series inexpensive.
        for dataset in ("evaluation", "held_out"):
            subset = handler_references.c.set_name == dataset
            chosen = conn.scalar(
                select(handler_references.c.source_id).where(subset).limit(1)
            )
            conn.execute(
                handler_references.delete().where(
                    subset, handler_references.c.source_id != chosen
                )
            )


def configure_judge(monkeypatch, override):
    if not override:
        monkeypatch.setattr(
            Judge,
            "batch",
            lambda self, states: [
                Grade(
                    grade=1.25,
                    letter="C",
                    same_grounds=0.5,
                    same_section=0.4,
                    judge_cost=Decimal("0.0001"),
                )
                for _ in states
            ],
        )


@pytest.mark.parametrize("override", [None, "openrouter/free"])
def test_real_dispatcher_judge_round_transitions_and_restart(
    tmp_path, monkeypatch, override
):
    monkeypatch.delenv("MODEL_NAME", raising=False)
    if override:
        monkeypatch.setenv("MODEL_NAME", override)
    app = create_app(tmp_path / "desk.sqlite3")
    with SignedClient(app) as desk:
        store = app.state.store
        choose_one_claim_per_set(store)
        monkeypatch.setattr(main, "backend", lambda: nullcontext(desk))

        def local(method, path, body=None):
            result = desk.request(method, path, json=body)
            result.raise_for_status()
            return result.json()

        monkeypatch.setattr(improver_api, "request", local)
        configure_judge(monkeypatch, override)
        claim_decision = call("record_decision", decision(0, "refer"))
        claim_decision.response_metadata = {
            "token_usage": {"cost": 0 if override else 0.0004}
        }
        claim_configs = []

        def configured_model(config):
            claim_configs.append((config.model_name, config.reasoning_effort))
            return StubModel(messages=iter([claim_decision]))

        monkeypatch.setattr(main, "model", configured_model)
        change = {
            "model": override or main.DEFAULT_MODEL,
            "reasoning_effort": "default" if override else "low",
            "instructions": "Be precise.",
            "reason": "Correct a recurring error.",
        }
        script = [
            tool_call("propose_change", change),
            tool_call("propose_change", change),
            tool_call("run_evaluation", {}),
            tool_call(
                "propose_recipe",
                {"recipe": "Review evidence first.", "reason": "Improve review."},
            ),
            AIMessage(content="Done"),
        ]
        if override:
            for message in script:
                message.response_metadata = {"token_usage": {"cost": 0}}
        turns = iter(script)
        monkeypatch.setattr(
            improver, "improver_model", lambda: StubModel(messages=turns)
        )
        with TestClient(service.create_service()) as agent:
            opened = agent.post("/improvement/start", json={"handler_id": "HND-01"})
            assert opened.status_code == 202
            rid = opened.json()["id"]
        path = f"/api/improver/rounds/{rid}"
        for choice, reason, expected in (
            ("reject", "Use clearer wording.", "configuration"),
            ("approve", "", "configuration"),
            ("approve", "", "recipe"),
        ):
            row = desk.get(path).json()
            assert row["proposal"]["kind"] == expected
            with TestClient(service.create_service()) as agent:
                accepted = agent.post(
                    f"/improvement/{rid}/review",
                    json={
                        "proposal_id": row["proposal"]["id"],
                        "handler_id": "HND-01",
                        "choice": choice,
                        "reason": reason,
                    },
                )
                assert accepted.status_code == 202
        finished = desk.get("/api/improvement").json()["rounds"][0]
        assert finished["id"] == rid
        assert finished["status"] == "finished"
        assert finished["verdict"] == "rolled_back"
        assert len(finished["reviews"]) == 3
        assert claim_configs == [(change["model"], None if override else "low")] * 6
        if override:
            assert finished["after"]["grade"] is None
            assert finished["costs"]["total"] == 0
        else:
            assert finished["costs"]["judge"] == 0.0006
            assert finished["costs"]["claim"] == 0.0024
            assert finished["costs"]["improver"] > 0
        assert len(store.all("round_evaluations")) == 6
        assert len(store.queue()) == 25
