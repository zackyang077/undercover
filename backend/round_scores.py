"""Score completed whole-set round evaluations and compare candidate versions."""

from collections import defaultdict
from decimal import Decimal
from statistics import mean

from sqlalchemy import select

from backend.db import model_usage, swarm_runs
from backend.scoreboard import view
from backend.swarm_metrics import measure

METRICS = ("accuracy", "grade", "cost_per_claim", "seconds_per_claim")
QUALITY = ("accuracy", "grade")


def score_run(evaluation):
    score = evaluation.score
    return {
        "accuracy": score.accuracy,
        "grade": score.grade,
        "cost_per_claim": float(score.model_cost_per_claim)
        if score.model_cost_complete and score.model_cost_per_claim is not None
        else None,
        "seconds_per_claim": score.work_ms_per_claim / 1000
        if score.work_complete and score.work_ms_per_claim is not None
        else None,
    }


def averages(runs):
    if not runs:
        return None
    result = {}
    for key in METRICS:
        values = [row["scores"][key] for row in runs if row["scores"][key] is not None]
        result[key] = mean(values) if values else None
    return result


def qualified(store, version, dataset):
    return sorted(
        (
            row
            for row in store.all("round_evaluations")
            if row["version"] == version
            and row["dataset"] == dataset
            and row["status"] == "completed"
            and row["size"] == "full"
        ),
        key=lambda row: row["created_at"],
    )


def compare(before, after, settings):
    if (
        not before
        or not after
        or any(after[k] is None or before[k] is None for k in METRICS)
    ):
        return False, "Held-out measurements are incomplete."
    missed = [k for k, floor in settings["objectives"].items() if after[k] < floor]
    if missed:
        return False, "Below objective: " + ", ".join(missed)
    better, worse = [], []
    for key in METRICS:
        old, new, noise, tolerance = (
            Decimal(str(val))
            for val in (
                before[key],
                after[key],
                settings["noise"][key],
                settings["tolerance"][key],
            )
        )
        if key in QUALITY:
            gain, meaningful, allowed = new - old, noise, tolerance
        else:
            gain, meaningful, allowed = old - new, old * noise, old * tolerance
        if gain > meaningful:
            better.append(key)
        if gain < -allowed:
            worse.append(key)
    if worse:
        return False, "Regression beyond tolerance: " + ", ".join(worse)
    if better:
        return True, "Improved beyond noise: " + ", ".join(better)
    return False, "No improvement exceeded its noise threshold."


def evaluation_report(store, run_id, *, details=False):
    with store.connection() as connection:
        run = (
            connection.execute(select(swarm_runs).where(swarm_runs.c.run_id == run_id))
            .mappings()
            .one_or_none()
        )
        return view(store, run, details=details) if run else None


def example_claims(store, version):
    rows = qualified(store, version, "evaluation")
    if not rows:
        return {"wrong": [], "explained_least_well": []}
    report = evaluation_report(store, rows[-1]["run_id"], details=True)
    items = (
        [item.model_dump(mode="json") for item in report.claims]
        if report and report.dataset == "evaluation"
        else []
    )
    wrong = [item for item in items if not item["accurate"]]
    right = sorted(
        (item for item in items if item["accurate"] and item["grade"] is not None),
        key=lambda item: item["grade"],
    )
    return {"wrong": wrong, "explained_least_well": right[:5]}


def unit_cost(store, model, current_model, catalog):
    with store.connection() as connection:
        costs = connection.execute(
            select(model_usage, swarm_runs.c.model)
            .join(swarm_runs, model_usage.c.run_id == swarm_runs.c.run_id)
            .where(swarm_runs.c.model.in_([model, current_model]))
        ).mappings()
        by_model = defaultdict(list)
        for row in costs:
            by_model[(row["model"], row["run_id"])].append(row)

    def maximum(name):
        observed = []
        for (model_name, _), calls in by_model.items():
            if model_name == name:
                totals, _ = measure(calls, [], "completed")
                observed.extend(
                    float(item.cost)
                    for item in totals.values()
                    if item.cost is not None
                )
        return max(observed, default=None)

    known = maximum(model)
    if known is not None or model == current_model:
        return known
    reference = maximum(current_model)
    if reference is None:
        return None
    prices = {row["model"]: row for row in catalog}
    ratios = [
        Decimal(str(prices[model][key])) / Decimal(str(prices[current_model][key]))
        for key in ("input_price", "output_price")
        if prices[current_model][key] > 0
    ]
    return float(Decimal(str(reference)) * max(ratios)) if ratios else None
