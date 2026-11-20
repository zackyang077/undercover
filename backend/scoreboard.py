"""Read evaluation outcomes alongside the backend's reference decisions."""

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.db import records, swarm_claims, swarm_runs
from backend.eval_sources import (
    EvalSet,
    EvalSize,
    evaluation_copies,
    handler_references,
)
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
            saved,
            (saved.c.kind == "decisions") & (saved.c.id == swarm_claims.c.claim_id),
        )
        .where(swarm_claims.c.run_id == run_id)
        .order_by(swarm_claims.c.position)
    )


def view(store: Store, run, *, details: bool) -> EvaluationRun:
    with store.connection() as connection:
        version = store.find(connection, "run_configurations", run["run_id"])
        usage, work = Telemetry(store).for_run(run["run_id"])
        metrics, elapsed = measure(usage, work, run["status"])
        rows = evaluation_rows(connection, run["run_id"], run["dataset"])
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
                        model_cost=str(data.cost) if data.cost is not None else None,
                        work_ms=data.work_ms,
                    )
                )
    score = score_evaluation(inputs, elapsed) if run["status"] != "running" else None
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
