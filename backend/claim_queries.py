"""Read the queue projection, one linked claim, and its customer's prior filings."""

from sqlalchemy import (
    JSON,
    bindparam,
    case,
    cast,
    func,
    select,
    type_coerce,
)

from backend.db import attempts, records, runs
from backend.eval_sources import evaluation_copies


def named(alias, kind, record_id):
    return (alias.c.kind == kind) & (alias.c.id == record_id)


class ClaimQueries:
    def __init__(self, postgres: bool, max_attempts: int):
        claim = records.alias("claim")
        customer = records.alias("customer")
        decision = records.alias("decision")
        signed = records.alias("signed")
        payment = records.alias("payment")
        company = records.alias("company")
        policy = records.alias("policy")
        plan = records.alias("plan")
        settings = records.alias("plan_settings")
        evaluation_copy = (
            select(evaluation_copies.c.copy_id)
            .where(evaluation_copies.c.copy_id == claim.c.id)
            .exists()
        )

        def doc(alias):
            # Existing pilot records store JSON as TEXT on both databases.
            return (
                cast(alias.c.body, JSON)
                if postgres
                else type_coerce(alias.c.body, JSON)
            )

        claim_doc = doc(claim)
        customer_id = claim_doc["customer_id"].as_string()
        filed = claim_doc["submitted_on"].as_string()
        outcome = doc(decision)["outcome"].as_string()
        payout = doc(decision)["payout_amount"].as_float()
        limit = func.coalesce(
            doc(decision)["approval_limit"].as_float(),
            doc(company)["payout_approval_limit"].as_float(),
        )
        unpaid = (outcome == "approve") & payment.c.id.is_(None)
        running = (
            select(runs.c.thread_id)
            .where(runs.c.claim_id == claim.c.id, runs.c.status == "running")
            .exists()
        )
        failures = (
            select(func.count())
            .select_from(runs.join(attempts, runs.c.thread_id == attempts.c.thread_id))
            .where(runs.c.claim_id == claim.c.id, runs.c.status == "failed")
            .scalar_subquery()
        )
        status = case(
            (unpaid & signed.c.id.is_not(None), "in_progress"),
            (unpaid & (payout > limit), "waiting_sign_off"),
            (decision.c.id.is_not(None), "decided"),
            (running, "in_progress"),
            (failures >= max_attempts, "needs_handler"),
            else_="waiting",
        ).label("status")
        outcomes = claim.outerjoin(
            decision, named(decision, "decisions", claim.c.id)
        ).outerjoin(payment, named(payment, "payments", claim.c.id))
        financial = outcomes.outerjoin(
            signed, named(signed, "sign_offs", claim.c.id)
        ).join(company, named(company, "settings", "company"))
        self.settlement = (
            select(status, doc(signed).label("sign_off"), doc(payment).label("payment"))
            .select_from(financial)
            .where(named(claim, "claims", bindparam("requested_claim")))
        )
        linked = financial.join(customer, named(customer, "customers", customer_id))
        self.claims_projection = (
            select(
                claim.c.id.label("claim_id"),
                doc(customer)["name"].as_string().label("customer_name"),
                claim_doc["summary"].as_string().label("summary"),
                claim_doc["claim_type"].as_string().label("claim_type"),
                claim_doc["amount_claimed"].as_float().label("amount_claimed"),
                status,
                func.coalesce(outcome, "").label("outcome"),
                payout.label("payout_amount"),
                case(
                    (payment.c.id.is_not(None), "paid"),
                    (outcome == "approve", "unpaid"),
                    else_="not_applicable",
                ).label("payment_status"),
            )
            .select_from(linked)
            .where(claim.c.kind == "claims")
        )
        self.queue = self.claims_projection.where(~evaluation_copy).order_by(claim.c.id)
        full = (
            linked.join(
                policy, named(policy, "policies", claim_doc["policy_id"].as_string())
            )
            .join(plan, named(plan, "plans", doc(policy)["plan"].as_string()))
            .join(settings, named(settings, "settings", "plans"))
            .outerjoin(evaluation_copies, evaluation_copies.c.copy_id == claim.c.id)
        )
        self.detail = (
            select(
                claim.c.body.label("claim"),
                customer.c.body.label("customer"),
                policy.c.body.label("policy"),
                plan.c.body.label("plan"),
                settings.c.body.label("plans"),
                decision.c.body.label("decision"),
                signed.c.body.label("sign_off"),
                payment.c.body.label("payment"),
                limit.label("approval_limit"),
                evaluation_copies.c.source_id.label("evaluation_source_id"),
                evaluation_copies.c.run_id.label("evaluation_run_id"),
                status,
            )
            .select_from(full)
            .where(named(claim, "claims", bindparam("requested_claim")))
        )
        self.history = (
            select(
                claim.c.id.label("claim_id"),
                filed.label("filed_on"),
                claim_doc["summary"].as_string().label("summary"),
                func.coalesce(outcome, "open").label("outcome"),
                func.coalesce(doc(payment)["amount"].as_float(), 0).label(
                    "amount_paid"
                ),
            )
            .select_from(outcomes)
            .where(
                claim.c.kind == "claims",
                ~evaluation_copy,
                claim.c.id != bindparam("current_claim"),
                customer_id == bindparam("customer_id"),
                filed <= bindparam("filed_on"),
            )
        )
