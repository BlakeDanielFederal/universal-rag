"""Jira connector — ingests issues/sprints (what's on deck, status, scope).

Uses atlassian-python-api with ATLASSIAN_* env creds. Config options:
    projects:  list[str]  -> Jira project keys
    jql_extra: str        -> optional extra JQL ANDed onto the query

TODO(impl): JQL search with `updated >= cursor`, sprint/board enrichment,
field selection, pagination.
"""

from __future__ import annotations

from collections.abc import Iterator

from universal_rag.connectors.base import Connector, SourceDocument, SyncCursor


class JiraConnector(Connector):
    provider = "jira"

    def fetch(self, cursor: SyncCursor | None = None) -> Iterator[SourceDocument]:
        raise NotImplementedError("JiraConnector.fetch not yet implemented")
