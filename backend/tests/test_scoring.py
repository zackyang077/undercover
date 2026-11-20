from dataclasses import replace
from decimal import Decimal

from backend.scoring import (
    ClaimScore,
    OutcomeAmount,
    ScoreInput,
    score_claim,
    score_evaluation,
)


def score_input(
    agent: OutcomeAmount | None,
    handler: OutcomeAmount,
    *,
    worked: bool = True,
) -> ScoreInput:
    return ScoreInput(
        agent=agent,
        handler=handler,
        model_cost=Decimal("0"),
        model_cost_complete=True,
        work_ms=0,
        work_complete=True,
        worked=worked,
    )


def test_scores_match_hand_calculation_for_every_selected_claim():
    approve = OutcomeAmount("approve", 22_000)
    decline = OutcomeAmount("decline", 0)
    refer = OutcomeAmount("refer", 0)
    rows = [
        score_input(OutcomeAmount("approve", 21_900), approve),
        score_input(OutcomeAmount("approve", 22_101), approve),
        score_input(decline, refer),
        score_input(OutcomeAmount("approve", 125_000), decline),
        score_input(None, decline, worked=False),
        score_input(None, refer),
        score_input(decline, decline),
    ]
    assert [score_claim(row) for row in rows] == [
        ClaimScore(True, True, 100, True),
        ClaimScore(True, False, 101, True),
        ClaimScore(False, False, 150_000, True),
        ClaimScore(False, False, 125_000, True),
        ClaimScore(False, False, 5_000, False),
        ClaimScore(True, True, 0, False),
        ClaimScore(True, True, 0, True),
    ]

    result = score_evaluation(rows, elapsed_ms=400)

    assert result.claim_count == 7
    assert result.worked_count == 6
    assert result.decision_accuracy == 4 / 7
    assert result.accuracy == 3 / 7
    assert result.straight_through == 5 / 7
    assert result.cost_of_mistakes_cents == 280_201
    assert result.model_cost == Decimal("0")
    assert result.model_cost_per_claim == Decimal("0")
    assert result.model_cost_complete


def test_partial_work_and_cost_keep_known_amounts_and_selected_denominator():
    refer = OutcomeAmount("refer", 0)
    rows = [
        replace(score_input(refer, refer), model_cost=Decimal("0.01501"), work_ms=1300),
        replace(
            score_input(None, refer),
            model_cost=Decimal("0.00223"),
            model_cost_complete=False,
            work_ms=560,
            work_complete=False,
        ),
        replace(score_input(None, refer), model_cost=None, work_ms=400),
        score_input(None, refer, worked=False),
    ]

    result = score_evaluation(rows, elapsed_ms=1400)

    assert result.worked_count == 3
    assert result.model_cost == Decimal("0.01724")
    assert result.model_cost_per_claim == Decimal("0.00431")
    assert not result.model_cost_complete
    assert result.work_ms == 2260
    assert result.work_ms_per_claim == 565
    assert not result.work_complete
    assert result.elapsed_ms == 1400


def test_missing_report_is_unknown_when_no_cost_is_available():
    refer = OutcomeAmount("refer", 0)

    result = score_evaluation(
        [replace(score_input(refer, refer), model_cost=None)], 200
    )

    assert result.model_cost is None
    assert result.model_cost_per_claim is None
    assert not result.model_cost_complete
    assert result.work_complete


def test_no_selected_claims_have_undefined_averages():
    result = score_evaluation([], elapsed_ms=None)

    assert result.claim_count == 0
    assert result.worked_count == 0
    assert result.accuracy is None
    assert result.decision_accuracy is None
    assert result.straight_through is None
    assert result.model_cost_per_claim is None
    assert result.work_ms_per_claim is None
    assert result.elapsed_ms is None
