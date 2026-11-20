"""Versioned instructions, recipes and business settings owned by UnderCover."""

import difflib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from backend.db import records

Unit = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Grade = Annotated[float, Field(ge=0, le=3, allow_inf_nan=False)]
NonNegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class Objectives(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accuracy: Unit
    grade: Grade


class Margins(Objectives):
    cost_per_claim: NonNegative
    seconds_per_claim: NonNegative


class BusinessSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_limit: NonNegative
    objectives: Objectives
    noise: Margins
    tolerance: Margins
    budget: NonNegative
    concurrency: Annotated[int, Field(strict=True, ge=1, le=25)]


def timestamp():
    return datetime.now(UTC).isoformat()


def initialize(store, connection, data_path):
    if store.find(connection, "settings", "versions"):
        return
    initial = json.loads((data_path / "improvement.json").read_text(encoding="utf-8"))
    models = initial.pop("models")
    store.insert(connection, "settings", "models", {"models": models})
    store.insert(connection, "settings", "improvement", initial)
    store.insert(connection, "settings", "versions", {"configuration": 1, "recipe": 1})
    audit = {
        "version": 1,
        "reason": "Initial UnderCover configuration",
        "proposed_by": "Seed",
        "approved_by": "Seed",
        "created_at": timestamp(),
    }
    store.insert(
        connection,
        "configuration",
        "1",
        {
            **audit,
            "instructions": Path(__file__)
            .with_name("instructions.md")
            .read_text(encoding="utf-8"),
            "model": "openai/gpt-6-luna",
            "reasoning_effort": "low",
        },
    )
    store.insert(
        connection,
        "recipe",
        "1",
        {
            **audit,
            "reason": "Initial improvement recipe",
            "recipe": Path(__file__).with_name("recipe.md").read_text(encoding="utf-8"),
        },
    )
    limit = store.get("settings", "company")["payout_approval_limit"]
    for claim_id, body in connection.execute(
        select(records.c.id, records.c.body).where(records.c.kind == "decisions")
    ):
        decision = json.loads(body)
        decision.setdefault("approval_limit", limit)
        store.update(connection, "decisions", claim_id, decision)


def allowed_models(store):
    if name := os.getenv("MODEL_NAME"):
        return [
            {
                "model": name,
                "reasoning_efforts": ["default"],
                "input_price": 0,
                "output_price": 0,
            }
        ]
    return store.get("settings", "models")["models"]


def business_settings(store):
    return {
        **store.get("settings", "improvement"),
        "approval_limit": store.get("settings", "company")["payout_approval_limit"],
    }


def settings_view(store):
    return {
        "settings": business_settings(store),
        "models": allowed_models(store),
        "history": sorted(
            store.all("setting_changes"),
            key=lambda row: row["created_at"],
            reverse=True,
        ),
    }


def update_settings(store, request: BusinessSettings, handler_id: str):
    with store.connection(write=True) as connection:
        before = business_settings(store)
        after = request.model_dump()
        if after != before:
            company = store.get("settings", "company")
            company["payout_approval_limit"] = request.approval_limit
            store.update(connection, "settings", "company", company)
            store.update(
                connection,
                "settings",
                "improvement",
                request.model_dump(exclude={"approval_limit"}),
            )
            store.insert(
                connection,
                "setting_changes",
                str(uuid4()),
                {
                    "before": before,
                    "after": after,
                    "handler_id": handler_id,
                    "created_at": timestamp(),
                },
            )
        return settings_view(store)


def current(store, kind="configuration"):
    return store.get("settings", "versions")[kind]


def effective_config(store, version: int):
    row = store.get("configuration", str(version))
    config = {
        key: row[key]
        for key in ("version", "model", "reasoning_effort", "instructions")
    }
    if name := os.getenv("MODEL_NAME"):
        config.update(model=name, reasoning_effort="default")
    return config


def diff(old: str, new: str):
    return list(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile="Previous",
            tofile="Proposed",
            lineterm="",
        )
    )


def history(store, kind):
    rows = sorted(store.all(kind), key=lambda row: row["version"])
    text_key = "instructions" if kind == "configuration" else "recipe"
    for previous, new in zip(rows, rows[1:], strict=False):
        new["diff"] = diff(previous[text_key], new[text_key])
        if kind == "configuration":
            new["previous_model"] = previous["model"]
            new["previous_reasoning_effort"] = previous["reasoning_effort"]
    rows[0]["diff"] = []
    return rows[::-1]
