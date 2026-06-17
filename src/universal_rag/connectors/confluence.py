"""Confluence Data Center / Server connector.

Auth: Personal Access Token sent as ``Authorization: Bearer <PAT>`` (the DC/Server
scheme — Cloud's email+token basic auth is NOT used here).

Indexing scope is defined by the source's ``spaces`` (Confluence space keys) and
optional ``include_labels``. Incremental sync uses CQL ``lastmodified >= cursor``
so only pages changed since the last run are fetched.

Config (source.options):
    spaces:         list[str]   required — space keys to index
    include_labels: list[str]   optional — restrict to pages carrying these labels
    page_size:      int         optional — CQL page size (default 50)
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from universal_rag.config import get_settings
from universal_rag.connectors.base import Connector, SourceDocument, SyncCursor

# CQL `lastmodified` has minute granularity and is evaluated in the server's
# timezone, which may differ from our stored UTC cursor. Re-scan a small window
# on each run so edits near the boundary are never missed (re-fetched pages are
# hash-skipped downstream, so overlap is cheap).
_CURSOR_SAFETY = timedelta(minutes=5)

_EXPAND = "body.storage,version,space,history,history.lastUpdated"
_BLANK_LINES = re.compile(r"\n{3,}")
# Block-level tags whose boundaries should become line breaks; inline tags
# (strong/em/a/span/code/…) are left alone so words don't get split apart.
_BLOCK_TAGS = (
    "p",
    "div",
    "li",
    "ul",
    "ol",
    "br",
    "tr",
    "td",
    "th",
    "table",
    "blockquote",
    "section",
    "article",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
)


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested without any network)
# --------------------------------------------------------------------------- #
def build_cql(space: str, since: datetime | None, labels: list[str] | None) -> str:
    parts = [f'space = "{space}"', "type = page"]
    if since is not None:
        parts.append(f'lastmodified >= "{since:%Y-%m-%d %H:%M}"')
    if labels:
        joined = ", ".join(f'"{label}"' for label in labels)
        parts.append(f"label in ({joined})")
    return " and ".join(parts) + " order by lastmodified asc"


def html_to_text(storage_html: str) -> str:
    """Convert Confluence storage-format XHTML to readable plain text."""
    if not storage_html:
        return ""
    soup = BeautifulSoup(storage_html, "html.parser")
    # Mark block boundaries with newlines, then concatenate inline text with no
    # separator so spacing already present in text nodes is preserved.
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.append("\n")
    text = soup.get_text("")
    lines = [line.strip() for line in text.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def page_to_document(source_id: str, page: dict[str, Any], base_url: str) -> SourceDocument:
    """Map a Confluence content object into a normalized SourceDocument."""
    version = page.get("version") or {}
    links = page.get("_links") or {}
    site_base = links.get("base") or base_url.rstrip("/")
    webui = links.get("webui") or ""
    url = f"{site_base}{webui}" if webui else ""

    body = ((page.get("body") or {}).get("storage") or {}).get("value", "")
    space_key = (page.get("space") or {}).get("key", "")
    author = (version.get("by") or {}).get("displayName", "")

    metadata: dict[str, object] = {
        "doc_type": "confluence_page",
        "space": space_key,
        "page_id": str(page.get("id", "")),
        "version": version.get("number"),
        "author": author,
        "url": url,
    }
    return SourceDocument(
        source_id=source_id,
        provider="confluence",
        external_id=str(page.get("id", "")),
        title=page.get("title", ""),
        content=html_to_text(body),
        url=url,
        updated_at=_parse_iso(version.get("when")),
        metadata=metadata,
    )


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #
class ConfluenceDataCenterClient:
    """Thin REST client for Confluence DC/Server using PAT bearer auth."""

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

    def iter_search(self, cql: str, expand: str, page_size: int = 50) -> Iterator[dict[str, Any]]:
        """Yield content objects for a CQL query, paging by start/limit."""
        start = 0
        while True:
            data = self._get(
                "/rest/api/content/search",
                {"cql": cql, "expand": expand, "start": start, "limit": page_size},
            )
            results = data.get("results", [])
            yield from results
            limit = data.get("limit", page_size) or page_size
            if len(results) < limit:
                break
            start += limit

    def current_user(self) -> dict[str, Any]:
        return self._get("/rest/api/user/current", {})

    def close(self) -> None:
        self._client.close()


# --------------------------------------------------------------------------- #
# Connector
# --------------------------------------------------------------------------- #
class ConfluenceConnector(Connector):
    provider = "confluence"

    def _client(self) -> ConfluenceDataCenterClient:
        s = get_settings()
        if not s.confluence_base_url or not s.confluence_pat:
            raise RuntimeError(
                "Confluence not configured: set CONFLUENCE_BASE_URL and CONFLUENCE_PAT in .env"
            )
        return ConfluenceDataCenterClient(
            s.confluence_base_url, s.confluence_pat, verify_ssl=s.confluence_verify_ssl
        )

    def _spaces(self) -> list[str]:
        spaces = self.source.opt("spaces") or []
        if not spaces:
            raise RuntimeError(f"Confluence source '{self.source.id}' has no 'spaces' configured")
        return list(spaces)

    @staticmethod
    def _since(cursor: SyncCursor | None) -> datetime | None:
        if not cursor or not cursor.value:
            return None
        dt = _parse_iso(cursor.value)
        return dt - _CURSOR_SAFETY if dt else None

    def fetch(self, cursor: SyncCursor | None = None) -> Iterator[SourceDocument]:
        client = self._client()
        since = self._since(cursor)
        labels = self.source.opt("include_labels") or []
        page_size = int(self.source.opt("page_size", 50))
        try:
            for space in self._spaces():
                cql = build_cql(space, since, labels)
                for page in client.iter_search(cql, _EXPAND, page_size):
                    yield page_to_document(self.source.id, page, client.base_url)
        finally:
            client.close()

    def healthcheck(self) -> bool:
        try:
            self._client().current_user()
            return True
        except Exception:
            return False
