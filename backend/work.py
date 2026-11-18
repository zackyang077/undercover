"""The service reads durable work and acknowledges completed steps over HTTP."""

import json
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, true

from backend.db import events, records
from backend.db import runs as run_table
from backend.finance import now
from backend.queue_runs import AttemptEnd, QueueRuns
from backend.storage import MAX_ATTEMPTS


class QueueEnd(BaseModel):
    status: Literal["completed", "failed", "stopped"]
    summary: str
    error: str


def recoverable_claims(queue: QueueRuns):
    store = queue.store
    with store.connection() as conn:
        rows = conn.execute(
            select(run_table.c.claim_id, run_table.c.status).order_by(
                run_table.c.started_at
            )
        )
        latest = dict((row[0], row[1]) for row in rows)
        pending = {
            cid
            for cid, status in latest.items()
            if status in {"running", "interrupted"}
        }
        signed = {
            c.claim_id
            for c in store.queue()
            if c.outcome == "approve"
            and c.payment_status == "unpaid"
            and c.status != "waiting_sign_off"
        }
        batch = queue.current()
        if batch and batch.status == "running":
            pending.update(i.claim_id for i in batch.items if i.status == "running")
    return {
        "claims": [*sorted(signed), *(cid for cid in latest if cid in pending - signed)]
    }


def unfinished_queue_item(queue: QueueRuns, claim_id: str) -> bool:
    batch = queue.current()
    return bool(
        batch
        and batch.status == "running"
        and any(i.claim_id == claim_id and i.status == "running" for i in batch.items)
    )


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


def activate_thread(queue: QueueRuns, claim_id: str):
    store = queue.store
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
        if not (
            payable
            or row.run_status in {"running", "interrupted"}
            or unfinished_queue_item(queue, claim_id)
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
            thread = str(uuid4())
            conn.execute(
                run_table.insert().values(
                    thread_id=thread,
                    claim_id=claim_id,
                    status="running",
                    started_at=now(),
                    finished_at="",
                    error="",
                )
            )
            conn.execute(
                events.insert().values(
                    thread_id=thread,
                    kind="resumed",
                    tool_name="",
                    text="Continuing a saved decision",
                    created_at=now(),
                )
            )
        return {"claim_id": claim_id, "thread_id": thread}


def finish_claim(queue: QueueRuns, claim_id: str, thread_id: str, report: AttemptEnd):
    runs, store = queue.runs, queue.store
    with store.connection(write=True) as conn:
        if (
            conn.scalar(
                select(run_table.c.claim_id).where(run_table.c.thread_id == thread_id)
            )
            != claim_id
        ):
            raise HTTPException(404, "Claim attempt not found")
        batch = queue.current()
        if batch and any(i.thread_id == thread_id for i in batch.items):
            queue.finish_attempt(batch.queue_run_id, thread_id, report.error)
            return {"retry": False}
        result = runs.conclude(claim_id, thread_id, report.error)
        retry = (
            result.status == "failed"
            and not store.detail(claim_id).decision
            and store.failures(claim_id) < MAX_ATTEMPTS
        )
        if retry:
            runs.begin(conn, claim_id)
        return {"retry": bool(retry)}


def routes(queue: QueueRuns) -> APIRouter:
    router = APIRouter(prefix="/api/work")
    store = queue.store

    @router.get("", operation_id="list_recoverable_work")
    def pending():
        """Find active attempts and payable outstanding approvals."""
        return recoverable_claims(queue)

    @router.post("/{claim_id}/activate", operation_id="activate_claim_thread")
    def activate(claim_id: str):
        """Return the existing attempt to resume, including a legacy unpaid decision."""
        return activate_thread(queue, claim_id)

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
        """Reconcile a resumed claim with its queue item or named attempt."""
        return finish_claim(queue, claim_id, thread_id, report)

    @router.post("/queue/{queue_id}/close", operation_id="finish_service_queue")
    def close(queue_id: str, report: QueueEnd):
        """Save the final state of the queue coordinator."""
        queue.finish(queue_id, report.status, report.error, report.summary)
        return {"status": "recorded"}

    @router.post(
        "/queue/{queue_id}/continue", operation_id="continue_interrupted_queue"
    )
    def continue_queue(queue_id: str):
        """Reopen the interrupted batch after recovering its claim threads."""
        return queue.continue_interrupted(queue_id)

    return router
