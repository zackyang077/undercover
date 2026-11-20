"""Schedule missing baseline or candidate trials and enforce the round limit."""

from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select

from backend import configuration as config
from backend.db import model_usage, swarm_claims
from backend.eval_sources import (
    evaluation_copies,
    evaluation_grades,
    handler_references,
)
from backend.improvement import Rounds
from backend.round_scores import (
    averages,
    compare,
    evaluation_report,
    qualified,
    score_run,
    unit_cost,
)
from backend.swarm_metrics import measure

CALL_LIMIT = 30
REQUIRED_TRIALS = 3


class Evaluations:
    def __init__(self, store):
        self.store = store
        self.rounds = Rounds(store)

    def trials(self, round_id):
        return [
            r for r in self.store.all("round_evaluations") if r["round_id"] == round_id
        ]

    def costs(self, row):
        trials = self.trials(row["id"])
        ids = [r["run_id"] for r in trials]
        with self.store.connection() as conn:
            usage = conn.execute(
                select(model_usage).where(model_usage.c.run_id.in_(ids))
            ).mappings()
            metrics, _ = measure(usage, [], "running")
        claim = sum((item.cost or Decimal(0) for item in metrics.values()), Decimal(0))
        with self.store.connection() as conn:
            judge = conn.scalar(
                select(func.sum(evaluation_grades.c.judge_cost))
                .select_from(
                    evaluation_grades.join(
                        evaluation_copies,
                        evaluation_copies.c.copy_id == evaluation_grades.c.copy_id,
                    )
                )
                .where(evaluation_copies.c.run_id.in_(ids))
            ) or Decimal(0)
        improver = sum(
            Decimal(str(c["cost"] or 0)) for c in row.get("usage", {}).values()
        )
        return {
            "claim": float(claim),
            "judge": float(judge),
            "improver": float(improver),
            "total": float(claim + judge + improver),
        }

    def remaining(self, row):
        return Decimal(str(row["settings"]["budget"])) - Decimal(
            str(self.costs(row)["total"])
        )

    def reserve_call(self, round_id):
        with self.store.connection(write=True) as conn:
            row = self.rounds.require_open(round_id)
            if self.remaining(row) <= 0 or row.get("calls", 0) >= CALL_LIMIT:
                raise HTTPException(409, "Improver call or spending limit reached")
            row["calls"] = row.get("calls", 0) + 1
            self.rounds.save(conn, row)
            return {"calls": row["calls"]}

    def charge(self, round_id, call_id, cost):
        with self.store.connection(write=True) as conn:
            row = self.rounds.get(round_id)
            row.setdefault("usage", {}).setdefault(call_id, {"cost": cost})
            self.rounds.save(conn, row)
            return self.costs(row)

    def estimate(self, row, version):
        name = config.effective_config(self.store, version)["model"]
        source = config.effective_config(self.store, row["base_version"])["model"]
        return unit_cost(self.store, name, source, row["models"])

    def can_work(self, round_id, run_id):
        with self.store.connection():
            row = self.rounds.get(round_id)
            trial = self.store.get("round_evaluations", run_id)
            if trial["round_id"] != round_id or row["status"] not in {
                "candidate",
                "baseline",
            }:
                return {
                    "allowed": False,
                    "reason": "This round is no longer running evaluations.",
                }
            balance = self.remaining(row)
            if balance <= 0:
                return {
                    "allowed": False,
                    "reason": "The round reached its spending limit.",
                }
            if trial["cold_start"]:
                declined = self.cold_estimate_exceeds(row, trial, balance)
                if declined:
                    return {
                        "allowed": False,
                        "reason": "First claim exceeds the remaining cost estimate.",
                    }
            return {"allowed": True, "reason": ""}

    def cold_estimate_exceeds(self, row, trial, balance):
        with self.store.connection() as conn:
            statuses = list(
                conn.scalars(
                    select(swarm_claims.c.status).where(
                        swarm_claims.c.run_id == trial["run_id"]
                    )
                )
            )
        if not any(status not in {"waiting", "in_progress"} for status in statuses):
            return False
        unit = self.estimate(row, trial["version"])
        return (
            unit is not None
            and Decimal(str(unit)) * statuses.count("waiting") > balance
        )

    def next(self, round_id):
        with self.store.connection(write=True) as conn:
            row = self.rounds.require_open(round_id)
            if row["status"] not in {"baseline", "candidate"}:
                return self.outcome(row)
            trials = self.trials(round_id)
            version = row["version"] or row["base_version"]
            if any(
                t["version"] == version and t["status"] in {"failed", "stopped"}
                for t in trials
            ):
                return self.failed(conn, row, "The evaluation was stopped or failed.")
            pending = next((t for t in trials if t["status"] == "scheduled"), None)
            if pending:
                result = evaluation_report(self.store, pending["run_id"])
                if not result or result.status == "running":
                    return dict(pending)
                self.settle(conn, pending, result)
                if result.status != "completed":
                    return self.failed(
                        conn, row, "The evaluation was stopped or failed."
                    )
            sets = {
                ds: qualified(self.store, version, ds)
                for ds in ("evaluation", "held_out")
            }
            count = min(1, len(sets["evaluation"])) + min(2, len(sets["held_out"]))
            if count == REQUIRED_TRIALS:
                return self.resolve(conn, row, sets)
            dataset = "held_out" if sets["evaluation"] else "evaluation"
            return self.schedule(conn, row, version, dataset, count + 1)

    def schedule(self, conn, row, version, dataset, progress):
        estimate = self.estimate(row, version)
        with self.store.connection() as current:
            count = current.scalar(
                select(func.count())
                .select_from(handler_references)
                .where(handler_references.c.set_name == dataset)
            )
        remaining = self.remaining(row)
        if remaining <= 0 or (
            estimate is not None and Decimal(str(estimate)) * count > remaining
        ):
            return self.failed(
                conn, row, "The evaluation estimate exceeds the remaining round budget."
            )
        trial = {
            "run_id": str(uuid4()),
            "round_id": row["id"],
            "version": version,
            "dataset": dataset,
            "concurrency": row["settings"]["concurrency"],
            "size": "full",
            "cold_start": estimate is None,
            "status": "scheduled",
            "created_at": config.timestamp(),
            "judge_cost": 0,
            "scores": None,
        }
        self.store.insert(conn, "round_evaluations", trial["run_id"], trial)
        row["progress"] = progress
        self.rounds.save(conn, row)
        return trial

    def settle(self, conn, trial, result):
        trial["status"] = result.status
        score = result.score
        trial["judge_cost"] = float(score.judge_cost or 0) if score else 0
        trial["scores"] = (
            score_run(result) if score and result.status == "completed" else None
        )
        self.store.update(conn, "round_evaluations", trial["run_id"], trial)

    def record_close(self, conn, run_id):
        trial = self.store.find(conn, "round_evaluations", run_id)
        if trial and trial["status"] == "scheduled":
            self.settle(conn, trial, evaluation_report(self.store, run_id))

    def failed(self, conn, row, why):
        if row["version"] is None:
            row.update(
                status="stopped", stop_reason=why, ended_at=config.timestamp(), why=why
            )
        else:
            row.update(verdict="rolled_back", why=why, status="verdict")
        return self.outcome(self.rounds.save(conn, row))

    def resolve(self, conn, row, sets):
        scores = averages(sets["held_out"])
        if row["version"] is None:
            row.update(before=scores, status="working")
        else:
            keep, why = compare(row["before"], scores, row["settings"])
            row.update(
                verdict="kept" if keep else "rolled_back",
                after=scores,
                why=why,
                status="verdict",
            )
            if keep:
                self.rounds.select(conn, "configuration", row["version"])
        return self.outcome(self.rounds.save(conn, row))

    @staticmethod
    def outcome(row):
        return {
            "done": True,
            **{k: row[k] for k in ("status", "verdict", "why", "before", "after")},
        }
