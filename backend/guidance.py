"""Allowlisted, versioned policy and manual section lookup."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

if TYPE_CHECKING:
    from backend.storage import Store

DocumentName = Literal["wording", "manual"]
DOCUMENTS = {"wording": "policy_wording.md", "manual": "claims_manual.md"}


class Section(BaseModel):
    document: DocumentName
    version: str
    section_id: str
    title: str
    content: str


class SectionIndex(BaseModel):
    document: DocumentName
    version: str
    introduction: str
    sections: list[dict[str, str]]


def initialize(connection, root: Path):
    for document, filename in DOCUMENTS.items():
        saved = connection.execute(
            "SELECT 1 FROM records WHERE kind='guidance' AND id=?", (document,)
        ).fetchone()
        if saved:
            continue
        content = (root / filename).read_text(encoding="utf-8")
        introduction, *parts = re.split(r"(?m)^## (\d+)\. ([^\n]+)\n", content)
        version = re.search(r"Version (\d+\.\d+)", introduction)
        if version is None:
            raise ValueError(f"Missing version in {filename}")
        sections = [
            {
                "section_id": parts[i],
                "title": parts[i + 1],
                "content": parts[i + 2].strip(),
            }
            for i in range(0, len(parts), 3)
        ]
        connection.execute(
            "INSERT INTO records VALUES ('guidance',?,?)",
            (
                document,
                json.dumps(
                    {
                        "document": document,
                        "version": version[1],
                        "introduction": introduction.strip(),
                        "sections": sections,
                    }
                ),
            ),
        )


class Guidance:
    def __init__(self, store: Store):
        self.store = store

    def index(self, document: DocumentName) -> SectionIndex:
        data = self.store.get("guidance", document)
        return SectionIndex(
            document=document,
            version=data["version"],
            introduction=data["introduction"],
            sections=[
                {"section_id": s["section_id"], "title": s["title"]}
                for s in data["sections"]
            ],
        )

    def section(self, document: DocumentName, section_id: str) -> Section:
        data = self.store.get("guidance", document)
        found = next(
            (s for s in data["sections"] if s["section_id"] == section_id), None
        )
        if not found:
            raise HTTPException(404, "Guidance section not found")
        return Section(document=document, version=data["version"], **found)

    def search(self, document: DocumentName, query: str) -> list[dict[str, str]]:
        words = set(re.findall(r"[a-z0-9]+", query.lower()))
        data = self.store.get("guidance", document)
        matches = []
        for section in data["sections"]:
            text = section["title"] + " " + section["content"]
            score = sum(word in text.lower() for word in words)
            if score:
                matches.append(
                    (
                        score,
                        {
                            "section_id": section["section_id"],
                            "title": section["title"],
                            "excerpt": section["content"][:240],
                        },
                    )
                )
        return [match for _, match in sorted(matches, key=lambda m: -m[0])[:5]]


def routes(store: Store) -> APIRouter:
    router = APIRouter()
    guidance = Guidance(store)

    @router.get(
        "/api/guidance/{document}/sections", operation_id="list_guidance_sections"
    )
    def list_guidance_sections(document: DocumentName) -> SectionIndex:
        """List section titles and version metadata for one guidance document."""
        return guidance.index(document)

    @router.get("/api/guidance/{document}/search", operation_id="search_guidance")
    def search_guidance(document: DocumentName, query: str) -> list[dict[str, str]]:
        """Find relevant sections using words in a required search query."""
        return guidance.search(document, query)

    @router.get(
        "/api/guidance/{document}/sections/{section_id}",
        operation_id="read_guidance_section",
    )
    def read_guidance_section(document: DocumentName, section_id: str) -> Section:
        """Read one complete versioned guidance section."""
        return guidance.section(document, section_id)

    return router
