"""Confluence connector — ingests wiki pages (roadmaps, specs, plans).

Uses atlassian-python-api with ATLASSIAN_* env creds. Config options:
    spaces:         list[str]  -> Confluence space keys
    include_labels: list[str]  -> optional page-label filter

TODO(impl): page iteration via CQL with `lastModified >= cursor`, HTML->text,
attachment handling, pagination.
"""

from __future__ import annotations

from collections.abc import Iterator

from universal_rag.connectors.base import Connector, SourceDocument, SyncCursor


class ConfluenceConnector(Connector):
    provider = "confluence"

    def fetch(self, cursor: SyncCursor | None = None) -> Iterator[SourceDocument]:
        raise NotImplementedError("ConfluenceConnector.fetch not yet implemented")
