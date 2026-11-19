"""Aggregate the provider reports and measured work of one run."""

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.engine import RowMapping


@dataclass
class ClaimMetrics:
    known_cost: Decimal | None = None
    missing_cost: bool = False
    work_ms: int = 0
    work_complete: bool = True
    worked: bool = False

    @property
    def cost(self) -> Decimal | None:
        return self.known_cost if self.missing_cost else self.known_cost or Decimal(0)


def measure(
    usage: Iterable[RowMapping],
    segments: Iterable[RowMapping],
    status: str,
) -> tuple[dict[str, ClaimMetrics], int | None]:
    claims: defaultdict[str, ClaimMetrics] = defaultdict(ClaimMetrics)
    for row in usage:
        metrics = claims[row["claim_id"]]
        cost = json.loads(row["usage"]).get("cost") if row["usage"] else None
        if cost is None:
            metrics.missing_cost = True
        else:
            metrics.known_cost = (metrics.known_cost or Decimal(0)) + Decimal(str(cost))
    began, ended = [], []
    for row in segments:
        metrics = claims[row["claim_id"]]
        metrics.worked = True
        metrics.work_ms += row["elapsed_ms"]
        start = datetime.fromisoformat(row["started_at"])
        began.append(start)
        if row["finished_at"]:
            ended.append(datetime.fromisoformat(row["finished_at"]))
        else:
            metrics.work_complete = False
            metrics.missing_cost = True
            ended.append(start + timedelta(milliseconds=row["elapsed_ms"]))
    if not began:
        return dict(claims), None
    finish = datetime.now(UTC) if status == "running" else max(ended)
    elapsed_ms = max(0, round((finish - min(began)).total_seconds() * 1000))
    return dict(claims), elapsed_ms
