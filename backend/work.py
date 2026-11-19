"""The service reads durable work and acknowledges completed steps over HTTP."""

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, true

from backend.db import records
from backend.db import runs as run_table
from backend.runs import Runs
from backend.storage import MAX_ATTEMPTS
from backend.swarm import Swarm, swarm_claims


class AttemptEnd(BaseModel):
    error: str


def recoverable_claims(runs: Runs):
    store = runs.store
    with store.connection() as conn:
        rows = conn.execute(
            select(
                run_table.c.claim_id,
                run_table.c.status,
                select(swarm_claims.c.claim_id)
                .where(swarm_claims.c.thread_id == run_table.c.thread_id)
                .exists(),
            ).order_by(run_table.c.started_at)
        )
        latest = {claim: (status, in_swarm) for claim, status, in_swarm in rows}
        pending = {
            cid
            for cid, (status, in_swarm) in latest.items()
            if not in_swarm and status in {"running", "interrupted"}
        }
        signed = {
            c.claim_id
            for c in store.queue()
            if c.outcome == "approve"
            and c.payment_status == "unpaid"
            and c.status != "waiting_sign_off"
        }
    return {
        "claims": [*sorted(signed), *(cid for cid in latest if cid in pending - signed)]
    }


def signed_thread(store, claim_id: str):
    paid = records.alias("payment")
    query = (
        select(run_table.c.thread_id, records.c.body)
        .join(
            records,
            (records.c.id == run_table.c.claim_id) & (records.c.kind == "sign_offs"),
        )
        .where(
            run_table.c.claim_id == claim_id,
            run_table.c.status == "running",
            ~select(paid.c.id)
            .where(paid.c.kind == "payments", paid.c.id == claim_id)
            .exists(),
        )
    )
    with store.connection() as conn:
        row = conn.execute(query).one_or_none()
        return (
            {"thread_id": row.thread_id, "sign_off": json.loads(row.body)}
            if row
            else None
        )


def activate_thread(runs: Runs, claim_id: str):
    store = runs.store
    claims = store.queries.queue.order_by(None).subquery()
    latest = (
        select(run_table.c.thread_id, run_table.c.status.label("run_status"))
        .where(run_table.c.claim_id == claim_id)
        .order_by(run_table.c.started_at.desc())
        .limit(1)
        .subquery()
    )
    query = (
        select(
            claims.c.payment_status,
            claims.c.status,
            latest.c.thread_id,
            latest.c.run_status,
        )
        .select_from(claims.outerjoin(latest, true()))
        .where(claims.c.claim_id == claim_id)
    )
    with store.connection(write=True) as conn:
        row = conn.execute(query).one_or_none()
        if row is None:
            raise HTTPException(404, "Record not found")
        payable = row.payment_status == "unpaid" and row.status != "waiting_sign_off"
        if row.status == "waiting_sign_off" or not (
            payable or row.run_status in {"running", "interrupted"}
        ):
            return None
        if row.thread_id:
            thread = row.thread_id
            if row.run_status != "running":
                conn.execute(
                    run_table.update()
                    .where(run_table.c.thread_id == thread)
                    .values(status="running", finished_at="", error="")
                )
        else:
            thread = Swarm.start_payment(conn, claim_id)
        return {"claim_id": claim_id, "thread_id": thread}


def finish_claim(runs: Runs, claim_id: str, thread_id: str, report: AttemptEnd):
    store = runs.store
    with store.connection(write=True) as conn:
        if (
            conn.scalar(
                select(run_table.c.claim_id).where(run_table.c.thread_id == thread_id)
            )
            != claim_id
        ):
            raise HTTPException(404, "Claim attempt not found")
        result = runs.conclude(claim_id, thread_id, report.error)
        retry = (
            result.status == "failed"
            and not store.detail(claim_id).decision
            and store.failures(claim_id) < MAX_ATTEMPTS
        )
        if retry:
            runs.begin(conn, claim_id)
        return {"retry": bool(retry)}


def routes(runs: Runs) -> APIRouter:
    router = APIRouter(prefix="/api/work")
    store = runs.store

    @router.get("", operation_id="list_recoverable_work")
    def pending():
        """Find active attempts and payable outstanding approvals."""
        return recoverable_claims(runs)

    @router.post("/{claim_id}/activate", operation_id="activate_claim_thread")
    def activate(claim_id: str):
        """Return the existing attempt to resume, including a legacy unpaid decision."""
        return activate_thread(runs, claim_id)

    @router.get("/{claim_id}/sign-off", operation_id="read_saved_sign_off")
    def saved_sign_off(claim_id: str):
        """Read the committed handler sign-off for a waiting graph thread."""
        with store.connection() as conn:
            return {"sign_off": store.find(conn, "sign_offs", claim_id)}

    @router.get("/{claim_id}/signed-thread", operation_id="get_signed_thread")
    def read_signed_thread(claim_id: str):
        """Read the active unpaid thread and its committed handler sign-off."""
        return signed_thread(store, claim_id)

    @router.post("/{claim_id}/{thread_id}/finish", operation_id="finish_service_claim")
    def complete(claim_id: str, thread_id: str, report: AttemptEnd):
        """Reconcile an individually scheduled claim with its saved attempt."""
        return finish_claim(runs, claim_id, thread_id, report)

    return router
