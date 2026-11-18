"""Calculate a claim's eligible payout using UnderCover's policy and plan rules."""

from collections.abc import Iterator
from decimal import Decimal
from math import ceil
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from backend.schemas import Money
from backend.storage import Store

Benefit = Literal[
    "trip_cancellation",
    "trip_interruption",
    "trip_delay",
    "baggage_delay",
    "baggage",
    "passport",
    "emergency_medical",
]


class Line(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Annotated[str, Field(min_length=1)]
    amount: Annotated[
        Money, Field(description="Total claimed for the entire line in USD")
    ]
    kind: Literal[
        "item", "electronics", "cash", "expense", "hotel", "meals_without_receipts"
    ]
    quantity: Annotated[
        int, Field(ge=1, description="Number of items; one for a pair or set")
    ]
    nights: Annotated[int, Field(ge=0, description="Hotel nights, otherwise zero")]
    hours: Annotated[
        float,
        Field(
            ge=0,
            allow_inf_nan=False,
            description="Delay hours for meals without receipts, otherwise zero",
        ),
    ]


class PayoutInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benefit: Benefit
    lines: Annotated[
        list[Line], Field(description="Only the covered items and expenses")
    ]
    paid_by_others: Annotated[
        Money,
        Field(
            description="Amount other parties paid or refunded for this loss, or zero"
        ),
    ]


class Step(BaseModel):
    step: str
    amount: float


class Payout(BaseModel):
    calculation: list[Step]
    payout: float
    needs_sign_off: bool


def eligible(
    line: Line, per_item: Decimal, electronics: Decimal, travelers: int
) -> Decimal:
    match line.kind:
        case "item":
            return min(line.amount, per_item * line.quantity)
        case "electronics":
            return min(line.amount, electronics * line.quantity)
        case "hotel":
            return min(line.amount, Decimal(350) * ceil(travelers / 2) * line.nights)
        case "meals_without_receipts":
            return min(line.amount, Decimal(35) * travelers * ceil(line.hours / 24))
        case _:
            return line.amount


def covered_lines(
    lines: list[Line], policy: dict, plan: dict, plans: dict
) -> Iterator[tuple[str, Decimal]]:
    per_item = Decimal(str(plan["per_item_limit"]))
    electronics = per_item
    if "electronics_plus" in policy["add_ons"]:
        addon = next(a for a in plans["add_ons"] if a["add_on"] == "electronics_plus")
        electronics = Decimal(str(addon["electronics_per_item_limit"]))

    cash = Decimal(0)
    for line in lines:
        if line.kind == "cash":
            cash += line.amount
            continue
        allowed = eligible(line, per_item, electronics, len(policy["travelers"]))
        text = line.description
        if allowed < line.amount:
            text += f", limited to ${allowed:,.2f}"
        yield text, allowed
    if cash:
        yield "Cash, up to $100 in all", min(cash, Decimal(100))


def calculate(store: Store, claim_id: str, request: PayoutInput) -> Payout:
    claim = store.get("claims", claim_id)
    policy = store.get("policies", claim["policy_id"])
    plan = store.get("plans", policy["plan"])
    plans = store.get("settings", "plans")
    calculation: list[Step] = []
    total = Decimal(0)

    def add(text: str, amount: Decimal):
        nonlocal total
        total += amount
        calculation.append(Step(step=text, amount=float(amount)))

    for text, amount in covered_lines(request.lines, policy, plan, plans):
        add(text, amount)
    if request.paid_by_others:
        add("Less what others have paid or refunded", -request.paid_by_others)
    if request.benefit in plans["deductible_applies_to"] and total > 0:
        deductible = min(Decimal(str(plan["deductible"])), total)
        if deductible:
            add("Less the deductible", -deductible)
    benefit = request.benefit
    if benefit in ("trip_cancellation", "trip_interruption"):
        maximum = min(plan["trip_cancellation_max"], policy["trip"]["trip_cost"])
    else:
        maximum = plan[f"{benefit}_max"]
    ceiling = Decimal(str(maximum))
    if total > ceiling:
        add(f"Less the amount over the maximum of ${ceiling:,.0f}", ceiling - total)
    if total < 0:
        add("Nothing is left to pay", -total)
    limit = Decimal(str(store.get("settings", "company")["payout_approval_limit"]))
    return Payout(
        calculation=calculation, payout=float(total), needs_sign_off=total > limit
    )


def routes(store: Store) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/claims/{claim_id}/payout-calculation", operation_id="work_out_payout"
    )
    def work_out_payout(claim_id: str, request: PayoutInput) -> Payout:
        """Calculate a payout for the covered lines on this claim."""
        return calculate(store, claim_id, request)

    return router
