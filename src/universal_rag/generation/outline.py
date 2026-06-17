"""Structured outline — the format-agnostic backbone every renderer consumes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ArtifactType = Literal["email", "memo", "presentation"]


class Citation(BaseModel):
    """Provenance for a claim, traced back to a retrieved chunk."""

    source_id: str
    provider: str
    title: str
    url: str = ""
    chunk_id: int | None = None


class OutlineSection(BaseModel):
    heading: str
    bullets: list[str] = Field(default_factory=list)
    body: str = ""
    citations: list[Citation] = Field(default_factory=list)


class Outline(BaseModel):
    artifact_type: ArtifactType
    project_id: str
    title: str
    summary: str = ""
    sections: list[OutlineSection] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
