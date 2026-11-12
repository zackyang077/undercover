"""Typed records shared by the claims API."""

from pydantic import BaseModel, ConfigDict, JsonValue


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
    handler_id: str
    name: str
    can_approve_over_limit: bool


class ClaimDetail(BaseModel):
    claim: Claim
    policy: Policy
    customer: Customer
    plan: Plan
    add_ons: list[dict[str, JsonValue]]
    deductible_applies_to: list[str]
    approval_limit: float
    status: str
    decision: dict[str, JsonValue] | None = None
    sign_off: dict[str, JsonValue] | None = None
    payment: dict[str, JsonValue] | None = None


class ClaimSummary(BaseModel):
    claim_id: str
    customer_name: str
    summary: str
    claim_type: str
    amount_claimed: float
    status: str
    outcome: str
    payment_status: str


class Wording(BaseModel):
    version: str
    content: str
