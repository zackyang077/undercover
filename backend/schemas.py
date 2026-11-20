"""Typed records shared by the claims API."""

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["approve", "decline", "refer"]
    payout_amount: Money
    explanation: Annotated[str, Field(min_length=1)]
    calculation: Annotated[str, Field(min_length=1)]
    policy_references: Annotated[list[str], Field(min_length=1)]
    manual_references: Annotated[list[str], Field(min_length=1)]

    @model_validator(mode="after")
    def consistent_payout(self) -> Self:
        if (self.outcome == "approve") != (self.payout_amount > 0):
            raise ValueError(
                "Approval requires a positive payout; other outcomes require zero"
            )
        if any(
            ref not in {str(i) for i in range(1, 13)} for ref in self.policy_references
        ):
            raise ValueError("Policy references must be section numbers 1 through 12")
        if any(
            ref not in {str(i) for i in range(1, 15)} for ref in self.manual_references
        ):
            raise ValueError("Manual references must be section numbers 1 through 14")
        return self


class PaymentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]


class SignOffInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Record(BaseModel):
    """Preserve additional seed fields without discarding evidence."""

    model_config = ConfigDict(extra="allow")


class Item(Record):
    description: str
    category: str
    amount: float


class Document(Record):
    name: str
    type: str
    content: str


class Claim(Record):
    claim_id: str
    policy_id: str
    customer_id: str
    claim_type: str
    submitted_on: str
    incident_date: str
    incident_location: str
    summary: str
    story: str
    items: list[Item]
    documents: list[Document]
    amount_claimed: float
    status: str


class Trip(Record):
    destination: str
    departs_on: str
    returns_on: str
    trip_cost: float


class Policy(Record):
    policy_id: str
    customer_id: str
    plan: str
    add_ons: list[str]
    purchased_on: str
    trip: Trip
    travelers: list[str]
    status: str


class PreviousClaim(Record):
    claim_id: str
    filed_on: str
    summary: str
    outcome: str
    amount_paid: float


class Customer(Record):
    customer_id: str
    name: str
    email: str
    phone: str
    date_of_birth: str
    address: dict[str, str]
    previous_claims: list[PreviousClaim]


class Plan(Record):
    plan: str
    name: str
    deductible: float


class Handler(Record):
    email: str
    handler_id: str
    name: str
    can_approve_over_limit: bool


class Settlement(BaseModel):
    status: str
    sign_off: dict[str, JsonValue] | None = None
    payment: dict[str, JsonValue] | None = None


class ClaimDetail(Settlement):
    claim: Claim
    policy: Policy
    customer: Customer
    plan: Plan
    add_ons: list[dict[str, JsonValue]]
    deductible_applies_to: list[str]
    approval_limit: float
    evaluation_copy: bool = False
    evaluation_run_id: str | None = None
    decision: dict[str, JsonValue] | None = None


class ClaimSummary(BaseModel):
    claim_id: str
    customer_name: str
    summary: str
    claim_type: str
    amount_claimed: float
    status: str
    outcome: str
    payout_amount: float | None
    payment_status: str


class Wording(BaseModel):
    version: str
    content: str
