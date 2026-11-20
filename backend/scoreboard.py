"""Read evaluation outcomes alongside the backend's reference decisions."""

import json
from dataclasses import replace
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.db import records, swarm_claims, swarm_runs
from backend.eval_sources import (
    EvalSet,
    EvalSize,
    evaluation_copies,
    evaluation_grades,
    handler_references,
)
from backend.judge import Judge
from backend.scoring import (
    EvaluationScore,
    OutcomeAmount,
    ScoreInput,
    score_claim,
    score_evaluation,
)
from backend.storage import Store
from backend.swarm_metrics import ClaimMetrics, measure
from backend.telemetry import Telemetry


class EvaluationClaim(BaseModel):
    claim_id: str
    source_id: str
    status: str
    agent_outcome: str | None
    agent_payout_cents: int | None
    rationale: str
    handler_outcome: str
    handler_payout_cents: int
    handler_reason: str | None
    handler_reference: str | None
    agent_references: list[str]
    grade: float | None
    letter: str | None
    same_grounds: float | None
    same_section: float | None
    accurate: bool
    model_cost: str | None
    work_ms: int


class EvaluationRun(BaseModel):
    config_version: int | None = None
    run_id: str
    status: str
    concurrency: int
    dataset: EvalSet
    size: EvalSize
    model: str
    reasoning_effort: str | None
    created_at: str
    finished_at: str | None
    error: str
    score: EvaluationScore | None
    claims: list[EvaluationClaim]


def evaluation_rows(connection, run_id: str, dataset: str):
    saved = records.alias("saved_decision")
    refs = handler_references.c
    return connection.execute(
        select(
            swarm_claims.c.claim_id,
            swarm_claims.c.status,
            evaluation_copies.c.source_id,
            refs.decision,
            refs.payout_cents,
            refs.reason,
            refs.reference,
            evaluation_grades,
            saved.c.body,
        )
        .select_from(swarm_claims)
        .join(evaluation_copies, evaluation_copies.c.copy_id == swarm_claims.c.claim_id)
        .join(
            handler_references,
            (refs.set_name == dataset)
            & (refs.source_id == evaluation_copies.c.source_id),
        )
        .outerjoin(
            evaluation_grades, evaluation_grades.c.copy_id == swarm_claims.c.claim_id
        )
        .outerjoin(
            saved,
            (saved.c.kind == "decisions") & (saved.c.id == swarm_claims.c.claim_id),
        )
        .where(swarm_claims.c.run_id == run_id)
        .order_by(swarm_claims.c.position)
    )


def grade_evaluation(store: Store, run_id: str, judge: Judge, *, stopped: bool):
    # Reserve each copy before making paid calls. A close replay after a lost
    # response or process exit must never purchase the same explanation twice.
    with store.connection(write=True) as connection:
        dataset = connection.scalar(
            select(swarm_runs.c.dataset).where(
                swarm_runs.c.run_id == run_id, swarm_runs.c.status == "running"
            )
        )
        if not dataset:
            return
        rows = [
            r for r in evaluation_rows(connection, run_id, dataset) if r.copy_id is None
        ]
        if not rows:
            return
        connection.execute(
            evaluation_grades.insert(), [{"copy_id": r.claim_id} for r in rows]
        )
    states = []
    for row in rows:
        decision = json.loads(row.body) if row.body else None
        states.append(
            {
                "handler": {
                    "decision": row.decision,
                    "reason": row.reason,
                    "reference": row.reference,
                },
                "agent": {
                    "decision": decision["outcome"],
                    "rationale": decision["explanation"],
                    "section": decision["policy_references"],
                }
                if decision
                else None,
            }
        )
    grades = (
        [judge.grade(state, None) for state in states]
        if stopped
        else judge.batch(states)
    )
    with store.connection(write=True) as connection:
        for row, grade in zip(rows, grades, strict=True):
            connection.execute(
                evaluation_grades.update()
                .where(evaluation_grades.c.copy_id == row.claim_id)
                .values(**grade.model_dump())
            )


def view(store: Store, run, *, details: bool) -> EvaluationRun:
    with store.connection() as connection:
        version = store.find(connection, "run_configurations", run["run_id"])
        usage, work = Telemetry(store).for_run(run["run_id"])
        metrics, elapsed = measure(usage, work, run["status"])
        rows = evaluation_rows(connection, run["run_id"], run["dataset"]).all()
        inputs, items = [], []
        for row in rows:
            decision = json.loads(row.body) if row.body else None
            data = metrics.get(row.claim_id, ClaimMetrics())
            observed = ScoreInput(
                agent=OutcomeAmount(decision["outcome"], decision["payout_cents"])
                if decision
                else None,
                handler=OutcomeAmount(row.decision, row.payout_cents),
                model_cost=data.cost,
                model_cost_complete=not data.missing_cost,
                work_ms=data.work_ms,
                work_complete=data.work_complete,
                worked=data.worked or bool(decision),
            )
            inputs.append(observed)
            if details:
                items.append(
                    EvaluationClaim(
                        claim_id=row.claim_id,
                        source_id=row.source_id,
                        status="decided" if decision else row.status,
                        agent_outcome=decision["outcome"] if decision else None,
                        agent_payout_cents=decision["payout_cents"]
                        if decision
                        else None,
                        rationale=decision["explanation"] if decision else "",
                        handler_outcome=row.decision,
                        handler_payout_cents=row.payout_cents,
                        accurate=score_claim(observed).accurate,
                        handler_reason=row.reason
                        if run["dataset"] == "evaluation"
                        else None,
                        handler_reference=row.reference
                        if run["dataset"] == "evaluation"
                        else None,
                        agent_references=decision["policy_references"]
                        if decision
                        else [],
                        grade=row.grade,
                        letter=row.letter,
                        same_grounds=row.same_grounds,
                        same_section=row.same_section,
                        model_cost=str(data.cost) if data.cost is not None else None,
                        work_ms=data.work_ms,
                    )
                )
    score = score_evaluation(inputs, elapsed) if run["status"] != "running" else None
    if score:
        grades = [r.grade for r in rows if r.grade is not None]
        costs = [r.judge_cost for r in rows if r.judge_cost is not None]
        score = replace(
            score,
            grade=sum(grades) / len(grades) if grades else None,
            judge_cost=sum(costs, Decimal(0)) if costs else None,
        )
    return EvaluationRun.model_validate(
        {
            **run,
            "config_version": version["version"] if version else None,
            "score": score,
            "claims": items,
        }
    )


def routes(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api/evaluations")
    evaluations = select(swarm_runs).where(swarm_runs.c.kind == "evaluation")

    @router.get("", operation_id="list_evaluations")
    def list_evaluations() -> list[EvaluationRun]:
        """List evaluation settings and scores newest first."""
        with store.connection() as connection:
            rows = (
                connection.execute(
                    evaluations.order_by(
                        swarm_runs.c.created_at.desc(), swarm_runs.c.run_id
                    )
                )
                .mappings()
                .all()
            )
            return [view(store, row, details=False) for row in rows]

    @router.get("/{run_id}", operation_id="get_evaluation")
    def get_evaluation(run_id: str) -> EvaluationRun:
        """Read one evaluation and its claim-by-claim comparisons."""
        with store.connection() as connection:
            row = (
                connection.execute(evaluations.where(swarm_runs.c.run_id == run_id))
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise HTTPException(404, "Evaluation not found")
            return view(store, row, details=True)

    return router
