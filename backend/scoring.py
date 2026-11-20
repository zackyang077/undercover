"""Score selected evaluation claims from backend-owned decisions and usage."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Outcome = Literal["approve", "decline", "refer"]
PAYOUT_TOLERANCE_CENTS = 100
WRONGFUL_DECLINE_CENTS = 150_000
NEEDLESS_REFERRAL_CENTS = 5_000


@dataclass(frozen=True)
class OutcomeAmount:
    outcome: Outcome
    payout_cents: int


@dataclass(frozen=True)
class ScoreInput:
    agent: OutcomeAmount | None
    handler: OutcomeAmount
    model_cost: Decimal | None
    model_cost_complete: bool
    work_ms: int
    work_complete: bool
    worked: bool


@dataclass(frozen=True)
class ClaimScore:
    decision_match: bool
    accurate: bool
    mistake_cost_cents: int
    straight_through: bool


@dataclass(frozen=True)
class EvaluationScore:
    claim_count: int
    worked_count: int
    decision_accuracy: float | None
    accuracy: float | None
    cost_of_mistakes_cents: int
    straight_through: float | None
    model_cost: Decimal | None
    model_cost_per_claim: Decimal | None
    model_cost_complete: bool
    work_ms: int
    work_ms_per_claim: float | None
    work_complete: bool
    elapsed_ms: int | None


def score_claim(row: ScoreInput) -> ClaimScore:
    """Treat a missing saved decision as a referral only for scoring."""
    agent = row.agent or OutcomeAmount("refer", 0)
    difference = abs(agent.payout_cents - row.handler.payout_cents)
    decision_match = agent.outcome == row.handler.outcome
    if decision_match:
        mistake_cost = difference if agent.outcome == "approve" else 0
    else:
        mistake_cost = {
            "approve": agent.payout_cents,
            "decline": WRONGFUL_DECLINE_CENTS,
            "refer": NEEDLESS_REFERRAL_CENTS,
        }[agent.outcome]
    return ClaimScore(
        decision_match=decision_match,
        accurate=decision_match and difference <= PAYOUT_TOLERANCE_CENTS,
        mistake_cost_cents=mistake_cost,
        straight_through=agent.outcome != "refer",
    )


def score_evaluation(
    rows: Sequence[ScoreInput], elapsed_ms: int | None
) -> EvaluationScore:
    """Include every selected claim and retain the known part of partial totals."""
    scores = [score_claim(row) for row in rows]
    count = len(rows)
    work_ms = sum(row.work_ms for row in rows)
    known_costs = [row.model_cost for row in rows if row.model_cost is not None]
    model_cost = sum(known_costs, Decimal(0)) if known_costs else None
    return EvaluationScore(
        claim_count=count,
        worked_count=sum(row.worked for row in rows),
        decision_accuracy=sum(score.decision_match for score in scores) / count
        if count
        else None,
        accuracy=sum(score.accurate for score in scores) / count if count else None,
        cost_of_mistakes_cents=sum(score.mistake_cost_cents for score in scores),
        straight_through=sum(score.straight_through for score in scores) / count
        if count
        else None,
        model_cost=model_cost,
        model_cost_per_claim=model_cost / count if model_cost is not None else None,
        model_cost_complete=all(
            row.model_cost_complete and row.model_cost is not None for row in rows
        ),
        work_ms=work_ms,
        work_ms_per_claim=work_ms / count if count else None,
        work_complete=all(row.work_complete for row in rows),
        elapsed_ms=elapsed_ms,
    )
