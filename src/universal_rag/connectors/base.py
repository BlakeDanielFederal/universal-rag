"""Connector contract shared by every provider (Confluence, Jira, GitHub, ...).

A connector's only job is to yield normalized `SourceDocument`s for a source,
optionally limited to items changed since a cursor (incremental sync). It does
NOT chunk, embed, or persist — the ingestion pipeline owns that.
"""

from __future__ import annotations

import abc
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

from universal_rag.config.schema import SourceConfig


@dataclass(slots=True)
class SourceDocument:
    """One retrievable unit from a provider, before chunking/embedding."""

    source_id: str  # e.g. "apollo-jira"
    provider: str  # "confluence" | "jira" | "github"
    external_id: str  # stable id within the provider (page id, issue key, PR number)
    title: str
    content: str  # plain text / markdown body
    url: str = ""
    updated_at: datetime | None = None
    # Free-form metadata stored alongside the chunk (author, status, labels,
    # repo, doc_type, etc.) and usable as retrieval filters.
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class SyncCursor:
    """Opaque per-source incremental marker (usually a timestamp/ETag)."""

    value: str | None = None


class Connector(abc.ABC):
    """Base class for all source connectors."""

    provider: str

    def __init__(self, source: SourceConfig) -> None:
        self.source = source

    @abc.abstractmethod
    def fetch(self, cursor: SyncCursor | None = None) -> Iterator[SourceDocument]:
        """Yield documents; if `cursor` is set, only those changed since it."""
        raise NotImplementedError

    def healthcheck(self) -> bool:
        """Cheap auth/connectivity probe. Override per provider."""
        return True
