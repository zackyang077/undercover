"""Store reported provider usage and active work against the executing run."""

import json
from datetime import UTC
from typing import Annotated

from fastapi import APIRouter
from pydantic import AwareDatetime, BaseModel, Field, JsonValue
from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.db import model_usage, work_segments
from backend.storage import Store


class UsageInput(BaseModel):
    call_id: str
    claim_id: str
    run_id: str | None
    usage: dict[str, JsonValue] | None


class SegmentInput(BaseModel):
    segment_id: str
    claim_id: str
    run_id: str | None
    started_at: AwareDatetime
    finished_at: AwareDatetime | None
    elapsed_ms: Annotated[int, Field(ge=0)]


class Telemetry:
    def __init__(self, store: Store):
        self.engine = store.engine
        self.insert = (
            postgres_insert
            if self.engine.dialect.name == "postgresql"
            else sqlite_insert
        )

    def record_usage(self, thread_id: str, data: UsageInput) -> None:
        statement = self.insert(model_usage).values(
            thread_id=thread_id,
            call_id=data.call_id,
            claim_id=data.claim_id,
            run_id=data.run_id,
            usage=None if data.usage is None else json.dumps(data.usage),
        )
        with self.engine.begin() as connection:
            connection.execute(statement.on_conflict_do_nothing())

    def record_segment(self, thread_id: str, data: SegmentInput) -> None:
        statement = self.insert(work_segments).values(
            thread_id=thread_id,
            segment_id=data.segment_id,
            claim_id=data.claim_id,
            run_id=data.run_id,
            started_at=data.started_at.astimezone(UTC).isoformat(),
            finished_at=(
                data.finished_at.astimezone(UTC).isoformat()
                if data.finished_at
                else None
            ),
            elapsed_ms=data.elapsed_ms,
        )
        with self.engine.begin() as connection:
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        work_segments.c.thread_id,
                        work_segments.c.segment_id,
                    ],
                    set_={
                        "elapsed_ms": statement.excluded.elapsed_ms,
                        "finished_at": statement.excluded.finished_at,
                    },
                    where=and_(
                        work_segments.c.finished_at.is_(None),
                        work_segments.c.elapsed_ms <= data.elapsed_ms,
                    ),
                )
            )

    def for_run(self, run_id: str):
        with self.engine.begin() as connection:
            usage = (
                connection.execute(
                    select(model_usage).where(model_usage.c.run_id == run_id)
                )
                .mappings()
                .all()
            )
            segments = (
                connection.execute(
                    select(work_segments).where(work_segments.c.run_id == run_id)
                )
                .mappings()
                .all()
            )
        return usage, segments


def routes(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api/runs/{thread_id}")
    metrics = Telemetry(store)

    @router.post("/usage", operation_id="record_model_usage")
    def record_model_usage(thread_id: str, data: UsageInput) -> dict[str, str]:
        """Record usage reported for one model call."""
        metrics.record_usage(thread_id, data)
        return {"status": "ok"}

    @router.post("/work-segments", operation_id="record_work_segment")
    def record_work_segment(thread_id: str, data: SegmentInput) -> dict[str, str]:
        """Record active progress for one claim invocation."""
        metrics.record_segment(thread_id, data)
        return {"status": "ok"}

    return router
