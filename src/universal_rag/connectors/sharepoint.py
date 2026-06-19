"""SharePoint / OneDrive connector — ingests document-library files via Microsoft
Graph ("the greater business context": proposals, plans, decks, specs).

Auth: **app-only client credentials** (Azure AD app with admin-consented
Sites.Read.All + Files.Read.All). A token is acquired per sync run and sent as
``Authorization: Bearer``.

Indexing scope is the source's ``sites`` (SharePoint sites as ``host:/sites/Path``)
and ``onedrive_users`` (OneDrive owners by UPN); each resolves to one or more
drives whose document libraries are walked. Incremental sync filters drive items
by ``lastModifiedDateTime >= cursor`` (matches the timestamp-cursor pipeline).

Text is extracted from common business formats (Word/PDF/PowerPoint/text/HTML);
unsupported binaries yield no text and are skipped.

Config (source.options):
    sites:              list[str]  SharePoint sites "host:/sites/Path"
    onedrive_users:     list[str]  OneDrive owners by UPN  (at least one of sites/
                                   onedrive_users is required)
    include_extensions: list[str]  optional — override the default allowed set
    max_file_mb:        int        optional — skip files larger than this (default 10)
    page_size:          int        optional — Graph children page size (default 200)

Out of scope (follow-ups): SharePoint Pages/News (/sites/{id}/pages), deletions
(pipeline has no delete path), Graph /delta, and token refresh for >1h syncs.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from universal_rag.config import get_settings
from universal_rag.connectors.base import Connector, SourceDocument, SyncCursor
from universal_rag.connectors.extract import DEFAULT_EXTENSIONS, extract_text

# lastModifiedDateTime is UTC ISO8601; re-scan a small window each run so edits
# near the boundary are never missed (re-fetched files are hash-skipped).
_CURSOR_SAFETY = timedelta(minutes=5)
_SCOPE = "https://graph.microsoft.com/.default"


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested without any network)
# --------------------------------------------------------------------------- #
def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def item_to_document(
    source_id: str, drive_id: str, item: dict[str, Any], text: str, kind: str = "sharepoint"
) -> SourceDocument:
    """Map a Graph driveItem (+ extracted text) into a normalized SourceDocument."""
    parent = item.get("parentReference") or {}
    file_facet = item.get("file") or {}
    by = (item.get("lastModifiedBy") or {}).get("user") or {}
    metadata: dict[str, object] = {
        "doc_type": f"{kind}_file",
        "source_kind": kind,  # "sharepoint" | "onedrive"
        "drive_id": drive_id,
        "item_id": item.get("id", ""),
        "path": parent.get("path", ""),
        "name": item.get("name", ""),
        "size": item.get("size"),
        "mime": file_facet.get("mimeType", ""),
        "author": by.get("displayName", ""),
        "url": item.get("webUrl", ""),
    }
    return SourceDocument(
        source_id=source_id,
        provider="sharepoint",
        external_id=f"{drive_id}:{item.get('id', '')}",
        title=item.get("name", ""),
        content=text,
        url=item.get("webUrl", ""),
        updated_at=_parse_iso(item.get("lastModifiedDateTime")),
        metadata=metadata,
    )


def item_passes(
    item: dict[str, Any], since: datetime | None, allowed_ext: set[str], max_bytes: int
) -> bool:
    """Cheap metadata-only filter applied before downloading a file."""
    ext = os.path.splitext(item.get("name", ""))[1].lower()
    if ext not in allowed_ext:
        return False
    if max_bytes and (item.get("size") or 0) > max_bytes:
        return False
    if since is not None:
        dt = _parse_iso(item.get("lastModifiedDateTime"))
        if dt is not None and dt < since:
            return False
    return True


# --------------------------------------------------------------------------- #
# OAuth2 token (app-only client credentials)
# --------------------------------------------------------------------------- #
def acquire_app_token(
    tenant_id: str,
    client_id: str,
    client_secret: str,
    authority: str = "https://login.microsoftonline.com",
    *,
    transport: httpx.BaseTransport | None = None,
) -> str:
    url = f"{authority.rstrip('/')}/{tenant_id}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": _SCOPE,
    }
    with httpx.Client(transport=transport, timeout=30.0) as client:
        resp = client.post(url, data=data)
        resp.raise_for_status()
        return str(resp.json()["access_token"])


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #
def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500  # throttling / server errors; not 4xx
    return False


class MicrosoftGraphClient:
    """Thin Microsoft Graph REST client (bearer token, nextLink pagination)."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=True,  # /content 302-redirects to a download URL
            transport=transport,
        )

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _get(self, path_or_url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        resp = self._client.get(path_or_url, params=params)
        resp.raise_for_status()
        return resp

    def _iter(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        # First page uses `params`; subsequent pages follow the absolute
        # @odata.nextLink verbatim (passing params here would clobber its query).
        data = self._get(path, params).json()
        while True:
            yield from data.get("value", [])
            next_link = data.get("@odata.nextLink")
            if not next_link:
                return
            data = self._get(next_link).json()

    def resolve_site(self, host_path: str) -> dict[str, Any]:
        host, _, rel = host_path.partition(":")
        path = f"/sites/{host}:{rel}" if rel else f"/sites/{host}"
        return self._get(path).json()

    def list_site_drives(self, site_id: str) -> list[dict[str, Any]]:
        return self._get(f"/sites/{site_id}/drives").json().get("value", [])

    def get_user_drive(self, upn: str) -> dict[str, Any]:
        return self._get(f"/users/{upn}/drive").json()

    def iter_drive_items(self, drive_id: str, page_size: int = 200) -> Iterator[dict[str, Any]]:
        """Walk a drive depth-first, yielding file items (skips folders themselves)."""
        stack = [f"/drives/{drive_id}/root/children"]
        while stack:
            path = stack.pop()
            for item in self._iter(path, {"$top": page_size}):
                if "folder" in item:
                    stack.append(f"/drives/{drive_id}/items/{item['id']}/children")
                elif "file" in item:
                    yield item

    def download_item(self, drive_id: str, item_id: str) -> bytes:
        return self._get(f"/drives/{drive_id}/items/{item_id}/content").content

    def close(self) -> None:
        self._client.close()


# --------------------------------------------------------------------------- #
# Connector
# --------------------------------------------------------------------------- #
class SharePointConnector(Connector):
    provider = "sharepoint"

    def _client(self) -> MicrosoftGraphClient:
        s = get_settings()
        if not (s.msgraph_tenant_id and s.msgraph_client_id and s.msgraph_client_secret):
            raise RuntimeError(
                "Microsoft Graph not configured: set MSGRAPH_TENANT_ID, "
                "MSGRAPH_CLIENT_ID and MSGRAPH_CLIENT_SECRET in .env"
            )
        token = acquire_app_token(
            s.msgraph_tenant_id, s.msgraph_client_id, s.msgraph_client_secret, s.msgraph_authority
        )
        return MicrosoftGraphClient(s.msgraph_base_url, token)

    @staticmethod
    def _since(cursor: SyncCursor | None) -> datetime | None:
        if not cursor or not cursor.value:
            return None
        dt = _parse_iso(cursor.value)
        return dt - _CURSOR_SAFETY if dt else None

    def _resolve_drives(self, client: MicrosoftGraphClient) -> list[tuple[str, str]]:
        """Return (drive_id, kind) for every configured site library + OneDrive."""
        sites = self.source.opt("sites") or []
        users = self.source.opt("onedrive_users") or []
        if not sites and not users:
            raise RuntimeError(
                f"SharePoint source '{self.source.id}' needs 'sites' and/or 'onedrive_users'"
            )
        drives: list[tuple[str, str]] = []
        for host_path in sites:
            site = client.resolve_site(host_path)
            drives.extend((d["id"], "sharepoint") for d in client.list_site_drives(site["id"]))
        for upn in users:
            drives.append((client.get_user_drive(upn)["id"], "onedrive"))
        return drives

    def fetch(self, cursor: SyncCursor | None = None) -> Iterator[SourceDocument]:
        client = self._client()
        since = self._since(cursor)
        allowed = {
            e.lower() for e in (self.source.opt("include_extensions") or DEFAULT_EXTENSIONS)
        }
        max_bytes = int(self.source.opt("max_file_mb", 10)) * 1024 * 1024
        page_size = int(self.source.opt("page_size", 200))
        try:
            for drive_id, kind in self._resolve_drives(client):
                for item in client.iter_drive_items(drive_id, page_size):
                    if not item_passes(item, since, allowed, max_bytes):
                        continue
                    data = client.download_item(drive_id, item["id"])
                    text = extract_text(
                        item["name"], data, (item.get("file") or {}).get("mimeType", "")
                    )
                    if not text.strip():
                        continue
                    yield item_to_document(self.source.id, drive_id, item, text, kind)
        finally:
            client.close()

    def list_external_ids(self) -> set[str]:
        client = self._client()
        page_size = int(self.source.opt("page_size", 200))
        ids: set[str] = set()
        try:
            for drive_id, _kind in self._resolve_drives(client):
                for item in client.iter_drive_items(drive_id, page_size):
                    ids.add(f"{drive_id}:{item['id']}")
        finally:
            client.close()
        return ids

    def healthcheck(self) -> bool:
        try:
            self._client()._get("/sites/root")
            return True
        except Exception:
            return False
