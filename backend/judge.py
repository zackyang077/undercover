"""Grade explanations against the handler's reference with OpenRouter Decisions."""

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
from openrouter import OpenRouter
from openrouter.components import DecisionsNoulAnswer, DecisionsScoreAnswer
from openrouter.components.decisionsrequest import QuestionsTypedDict
from openrouter.components.decisionsscorequestion import CriterionTypedDict
from openrouter.decisions import Decisions
from openrouter.errors import OpenRouterError
from pydantic import BaseModel, Field

JEV = "typesafe/jev-1.13"
LETTERS = ("F", "C", "B", "A")
RUBRIC: list[CriterionTypedDict] = [
    "F: different grounds from the handler's reason, or it contradicts "
    "the handler's reason",
    "C: partly the same as the handler's reason: one of the handler's "
    "grounds or the section "
    "is missing, or it adds a ground the handler did not give",
    "B: the same grounds and the same section of the policy wording as "
    "the handler's reason",
    "A: the same grounds and section as the handler's reason, and clearly "
    "better expressed: "
    "clearer for the customer, with the section and the figures, shorter, "
    "and no jargon",
]
QUESTIONS: dict[str, QuestionsTypedDict] = {
    "grade": {
        "type": "score",
        "instructions": "An expert claims handler decided a travel "
        "insurance claim and gave "
        "`handler.reason`, resting on `handler.reference`. Grade how well "
        "`agent.rationale` "
        "explains the same decision.",
        "criteria": RUBRIC,
    },
    "same_grounds": {
        "type": "noul",
        "instructions": "`agent.rationale` gives the same main grounds "
        "for the decision "
        "as `handler.reason`",
    },
    "same_section": {
        "type": "noul",
        "instructions": "`agent.rationale` refers to the same section of "
        "the policy wording "
        "as `handler.reference`",
    },
}


class Grade(BaseModel):
    grade: float | None = Field(default=None, ge=0, le=3)
    letter: str | None = None
    same_grounds: float | None = Field(default=None, ge=0, le=1)
    same_section: float | None = Field(default=None, ge=0, le=1)
    judge_cost: Decimal | None = Field(default=None, ge=0)


class Judge:
    def __init__(self):
        self.origin = os.getenv("OPENROUTER_JUDGE_URL", "https://openrouter.ai").rstrip(
            "/"
        )
        self.free = bool(os.getenv("MODEL_NAME"))
        self.key = os.getenv("OPENROUTER_API_KEY")

    def grade(self, state: dict, decisions: Decisions | None) -> Grade:
        if self.free:
            return Grade(judge_cost=Decimal(0))
        if state["agent"] is None:
            return Grade(
                grade=0,
                letter="F",
                same_grounds=0,
                same_section=0,
                judge_cost=Decimal(0),
            )
        if decisions is None:
            return Grade()
        cost = None
        try:
            response = decisions.create(
                model=JEV,
                questions=QUESTIONS,
                state=state,
                server_url=self.origin,
                timeout_ms=10_000,
                retries=None,
            )
            if response.usage.cost is not None:
                cost = Decimal(str(response.usage.cost))
            answers = response.answers
            score = answers["grade"]
            grounds = answers["same_grounds"]
            section = answers["same_section"]
            if not isinstance(score, DecisionsScoreAnswer) or not score.probabilities:
                raise ValueError("Missing score probabilities")
            if not isinstance(grounds, DecisionsNoulAnswer) or not isinstance(
                section, DecisionsNoulAnswer
            ):
                raise ValueError("Missing grade checks")
            level = max(score.probabilities, key=score.probabilities.__getitem__)
            return Grade(
                grade=score.score,
                letter=LETTERS[int(level)],
                same_grounds=grounds.noul,
                same_section=section.noul,
                judge_cost=cost,
            )
        except (httpx.HTTPError, OpenRouterError, KeyError, ValueError, IndexError):
            return Grade(judge_cost=cost)

    def batch(self, states: list[dict]) -> list[Grade]:
        if self.free or not self.key or not any(state["agent"] for state in states):
            return [self.grade(state, None) for state in states]
        with (
            OpenRouter(api_key=self.key) as client,
            ThreadPoolExecutor(max_workers=len(states)) as pool,
        ):
            decisions = client.alpha.decisions
            return list(pool.map(lambda state: self.grade(state, decisions), states))
