"""GitHub connector — ingests PRs/issues/releases/READMEs (what's implemented).

Uses PyGithub with GITHUB_TOKEN. Config options:
    repos:   list[str]  -> "owner/name" repositories
    include: list[str]  -> subset of {pull_requests, issues, releases, readme}

TODO(impl): per-repo iteration filtered by `updated_at >= cursor`, map each
object type to a SourceDocument with rich metadata (state, labels, merged, etc.).
"""

from __future__ import annotations

from collections.abc import Iterator

from universal_rag.connectors.base import Connector, SourceDocument, SyncCursor


class GitHubConnector(Connector):
    provider = "github"

    def fetch(self, cursor: SyncCursor | None = None) -> Iterator[SourceDocument]:
        raise NotImplementedError("GitHubConnector.fetch not yet implemented")
