"""Durable improvement rounds and human-reviewed configuration changes."""

from uuid import uuid4

from fastapi import HTTPException

from backend import configuration as config
from backend.round_scores import averages, example_claims, qualified

CLOSED = {"finished", "stopped"}
WAITING = {"configuration_review", "recipe_review"}


def snapshot(row):
    fields = (
        "id",
        "started_at",
        "recipe_version",
        "base_version",
        "version",
        "reason",
        "before",
        "after",
        "verdict",
        "why",
        "status",
    )
    return {key: row[key] for key in fields}


class Rounds:
    def __init__(self, store):
        self.store = store

    def get(self, round_id):
        return self.store.get("rounds", round_id)

    def save(self, conn, row):
        self.store.update(conn, "rounds", row["id"], row)
        return row

    def require_open(self, round_id):
        row = self.get(round_id)
        if row["status"] in CLOSED:
            raise HTTPException(409, "The round has ended")
        return row

    def create(self, handler_id):
        with self.store.connection(write=True) as conn:
            if any(r["status"] not in CLOSED for r in self.store.all("rounds")):
                raise HTTPException(409, "An improvement round is already open")
            version = config.current(self.store)
            recipe = config.current(self.store, "recipe")
            row = {
                "id": str(uuid4()),
                "status": "baseline",
                "started_at": config.timestamp(),
                "started_by": handler_id,
                "base_version": version,
                "recipe_version": recipe,
                "recipe": self.store.get("recipe", str(recipe))["recipe"],
                "settings": config.business_settings(self.store),
                "models": config.allowed_models(self.store),
                "version": None,
                "reason": "",
                "before": None,
                "after": None,
                "verdict": None,
                "why": "",
                "proposal": None,
                "reviews": [],
                "progress": 0,
                "ended_at": None,
                "stopped_by": None,
            }
            self.store.insert(conn, "rounds", row["id"], row)
            return row

    def stop(self, round_id, reason, actor):
        with self.store.connection(write=True) as conn:
            row = self.require_open(round_id)
            row.update(
                status="stopped",
                ended_at=config.timestamp(),
                stop_reason=reason,
                stopped_by=actor,
            )
            return self.save(conn, row)

    def recover(self):
        with self.store.connection(write=True):
            for row in self.store.all("rounds"):
                if row["status"] not in CLOSED | WAITING:
                    self.stop(
                        row["id"], "Interrupted by service restart", "Agent service"
                    )

    def finish(self, round_id):
        with self.store.connection(write=True) as conn:
            row = self.require_open(round_id)
            if row["status"] in WAITING:
                raise HTTPException(409, "A proposal is waiting for review")
            if row["version"] is not None and not row["verdict"]:
                row.update(
                    verdict="rolled_back",
                    why="Candidate evaluations were not completed.",
                )
            row.update(status="finished", ended_at=config.timestamp())
            return self.save(conn, row)

    def proposal(self, round_id, kind, args):
        with self.store.connection(write=True) as conn:
            row = self.require_open(round_id)
            if row["proposal"] or (
                kind == "configuration"
                and (row["version"] or row["status"] != "working")
            ):
                raise HTTPException(
                    409, "A configuration cannot be proposed at this stage"
                )
            if kind == "recipe" and not row["verdict"]:
                raise HTTPException(
                    409, "Evaluate the configuration before proposing a recipe"
                )
            if kind == "configuration" and not any(
                m["model"] == args["model"]
                and args["reasoning_effort"] in m["reasoning_efforts"]
                for m in row["models"]
            ):
                raise HTTPException(
                    422, "Choose a model and reasoning effort from the allowed list"
                )
            previous = (
                config.effective_config(self.store, row["base_version"])
                if kind == "configuration"
                else {"recipe": row["recipe"]}
            )
            key = "instructions" if kind == "configuration" else "recipe"
            row["proposal"] = {
                "id": str(uuid4()),
                "kind": kind,
                "args": args,
                "previous": previous,
                "diff": config.diff(previous[key], args[key]),
                "created_at": config.timestamp(),
            }
            row["status"] = kind + "_review"
            return self.save(conn, row)

    def review(self, round_id, decision):
        with self.store.connection(write=True) as conn:
            row = self.require_open(round_id)
            proposal = row["proposal"]
            if not proposal or proposal["id"] != decision["proposal_id"]:
                raise HTTPException(
                    409, "This proposal is no longer waiting for approval"
                )
            if decision["choice"] == "reject" and not decision["reason"].strip():
                raise HTTPException(422, "Enter a reason for rejecting the proposal")
            at = config.timestamp()
            row["reviews"].append({**decision, "proposal": proposal, "created_at": at})
            row.update(proposal=None, status="working")
            if decision["choice"] == "approve":
                self.accept(conn, row, proposal, decision["handler_id"], at)
            return self.save(conn, row)

    def accept(self, conn, row, proposal, actor, at):
        kind = proposal["kind"]
        version = max(v["version"] for v in self.store.all(kind)) + 1
        self.store.insert(
            conn,
            kind,
            str(version),
            {
                **proposal["args"],
                "version": version,
                "proposed_by": "Improver",
                "approved_by": actor,
                "created_at": at,
                "proposed_at": proposal["created_at"],
            },
        )
        if kind == "recipe":
            self.select(conn, "recipe", version)
        else:
            row.update(
                version=version,
                reason=proposal["args"]["reason"],
                status="candidate",
                progress=0,
            )

    def select(self, conn, kind, version):
        pointers = self.store.get("settings", "versions")
        pointers[kind] = version
        self.store.update(conn, "settings", "versions", pointers)

    def results(self, round_id, remaining):
        row = self.get(round_id)
        version = row["base_version"]
        return {
            "current_version": config.effective_config(self.store, version),
            "scores": {
                ds: averages(qualified(self.store, version, ds))
                for ds in ("held_out", "evaluation")
            },
            "evaluation_set": example_claims(self.store, version),
            "earlier_rounds": [
                snapshot(r)
                for r in self.store.all("rounds")
                if r["started_at"] < row["started_at"]
            ],
            "settings": row["settings"],
            "models_allowed": row["models"],
            "budget_left": remaining,
            "rule": (
                "Meet the objectives, improve beyond noise on at least one measure, "
                "and stay within tolerance on every measure."
            ),
        }
