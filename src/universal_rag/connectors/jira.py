"""Jira Data Center / Server connector — ingests issues (sprints, on-deck, scope).

Auth: Personal Access Token sent as ``Authorization: Bearer <PAT>`` (the DC/Server
scheme — Cloud's email+token basic auth is NOT used here). Uses the v2 REST API,
where issue descriptions/comments are plain wiki-markup strings (Cloud's v3 ADF
JSON is a different shape and out of scope).

Indexing scope is the source's ``projects`` (Jira project keys) plus an optional
``jql_extra`` constraint. Incremental sync uses JQL ``updated >= cursor`` so only
issues changed since the last run are fetched.

Config (source.options):
    projects:         list[str]  required — project keys to index
    jql_extra:        str        optional — extra JQL ANDed onto the query
    include_comments: bool       optional — fold comments into text (default True)
    page_size:        int        optional — search page size (default 50)
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from universal_rag.config import get_settings
from universal_rag.connectors.base import Connector, SourceDocument, SyncCursor

# JQL `updated` has minute granularity and is evaluated in the server's timezone,
# which may differ from our stored UTC cursor. Re-scan a small window each run so
# edits near the boundary are never missed (re-fetched issues are hash-skipped).
_CURSOR_SAFETY = timedelta(minutes=5)

# Fields we pull; description/comment carry the prose, the rest become metadata.
_FIELDS = (
    "summary,description,status,issuetype,priority,assignee,reporter,"
    "labels,components,fixVersions,created,updated,resolution,comment"
)
# Jira datetime offsets come without a colon, e.g. "2024-03-01T09:30:00.000+0000".
_OFFSET_NO_COLON = re.compile(r"([+-]\d{2})(\d{2})$")


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested without any network)
# --------------------------------------------------------------------------- #
def build_jql(projects: list[str], since: datetime | None, jql_extra: str | None) -> str:
    quoted = ", ".join(f'"{p}"' for p in projects)
    parts = [f"project in ({quoted})"]
    if since is not None:
        parts.append(f'updated >= "{since:%Y/%m/%d %H:%M}"')
    if jql_extra and jql_extra.strip():
        parts.append(f"({jql_extra.strip()})")
    return " AND ".join(parts) + " ORDER BY updated ASC"


def _parse_jira_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = _OFFSET_NO_COLON.sub(r"\1:\2", value)
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _issue_text(fields: dict[str, Any], *, include_comments: bool) -> str:
    blocks: list[str] = []
    if summary := fields.get("summary"):
        blocks.append(str(summary))
    if description := fields.get("description"):
        blocks.append(str(description))
    if include_comments:
        comments = ((fields.get("comment") or {}).get("comments")) or []
        for c in comments:
            author = (c.get("author") or {}).get("displayName", "")
            body = c.get("body", "")
            if body:
                blocks.append(f"Comment by {author}: {body}" if author else f"Comment: {body}")
    return "\n\n".join(blocks).strip()


def issue_to_document(
    source_id: str, issue: dict[str, Any], base_url: str, *, include_comments: bool = True
) -> SourceDocument:
    """Map a Jira issue into a normalized SourceDocument."""
    key = issue.get("key", "")
    fields = issue.get("fields") or {}
    summary = fields.get("summary", "")

    def _name(obj: Any) -> str:
        return obj.get("name", "") if isinstance(obj, dict) else ""

    def _display(obj: Any) -> str:
        return obj.get("displayName", "") if isinstance(obj, dict) else ""

    metadata: dict[str, object] = {
        "doc_type": "jira_issue",
        "key": key,
        "project": key.split("-")[0] if "-" in key else "",
        "status": _name(fields.get("status")),
        "issue_type": _name(fields.get("issuetype")),
        "priority": _name(fields.get("priority")),
        "assignee": _display(fields.get("assignee")),
        "reporter": _display(fields.get("reporter")),
        "resolution": _name(fields.get("resolution")),
        "labels": fields.get("labels") or [],
        "components": [_name(c) for c in (fields.get("components") or [])],
        "fix_versions": [_name(v) for v in (fields.get("fixVersions") or [])],
        "created": fields.get("created"),
        "url": f"{base_url.rstrip('/')}/browse/{key}",
    }
    return SourceDocument(
        source_id=source_id,
        provider="jira",
        external_id=key,
        title=f"{key}: {summary}" if key else summary,
        content=_issue_text(fields, include_comments=include_comments),
        url=str(metadata["url"]),
        updated_at=_parse_jira_dt(fields.get("updated")),
        metadata=metadata,
    )


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #
class JiraDataCenterClient:
    """Thin REST client for Jira DC/Server using PAT bearer auth (v2 API)."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        verify_ssl: bool = True,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            verify=verify_ssl,
            timeout=timeout,
            transport=transport,
        )

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def iter_search(self, jql: str, fields: str, page_size: int = 50) -> Iterator[dict[str, Any]]:
        """Yield issues for a JQL query, paging by startAt/maxResults."""
        start = 0
        while True:
            data = self._get(
                "/rest/api/2/search",
                {"jql": jql, "fields": fields, "startAt": start, "maxResults": page_size},
            )
            issues = data.get("issues", [])
            yield from issues
            total = data.get("total", 0)
            start += len(issues)
            if not issues or start >= total:
                break

    def current_user(self) -> dict[str, Any]:
        return self._get("/rest/api/2/myself", {})

    def close(self) -> None:
        self._client.close()


# --------------------------------------------------------------------------- #
# Connector
# --------------------------------------------------------------------------- #
class JiraConnector(Connector):
    provider = "jira"

    def _client(self) -> JiraDataCenterClient:
        s = get_settings()
        if not s.jira_base_url or not s.jira_pat:
            raise RuntimeError("Jira not configured: set JIRA_BASE_URL and JIRA_PAT in .env")
        return JiraDataCenterClient(s.jira_base_url, s.jira_pat, verify_ssl=s.jira_verify_ssl)

    def _projects(self) -> list[str]:
        projects = self.source.opt("projects") or []
        if not projects:
            raise RuntimeError(f"Jira source '{self.source.id}' has no 'projects' configured")
        return list(projects)

    @staticmethod
    def _since(cursor: SyncCursor | None) -> datetime | None:
        if not cursor or not cursor.value:
            return None
        dt = _parse_jira_dt(cursor.value)
        return dt - _CURSOR_SAFETY if dt else None

    def fetch(self, cursor: SyncCursor | None = None) -> Iterator[SourceDocument]:
        client = self._client()
        since = self._since(cursor)
        jql = build_jql(self._projects(), since, self.source.opt("jql_extra"))
        include_comments = bool(self.source.opt("include_comments", True))
        page_size = int(self.source.opt("page_size", 50))
        try:
            for issue in client.iter_search(jql, _FIELDS, page_size):
                yield issue_to_document(
                    self.source.id, issue, client.base_url, include_comments=include_comments
                )
        finally:
            client.close()

    def healthcheck(self) -> bool:
        try:
            self._client().current_user()
            return True
        except Exception:
            return False
