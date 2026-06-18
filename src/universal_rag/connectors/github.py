"""GitHub connector — ingests PRs / issues / releases / README ("what's shipped").

Auth: a Personal Access Token (classic or fine-grained) sent as
``Authorization: Bearer <token>``. Works against github.com (default) or GitHub
Enterprise Server via ``GITHUB_API_URL`` (``https://<host>/api/v3``).

Indexing scope is the source's ``repos`` ("owner/name") and ``include`` (which of
{pull_requests, issues, releases, readme}). Incremental sync uses the issues
endpoint's ``since`` (which covers both issues and PRs); releases are filtered by
publish time against the cursor, and the README is always fetched (hash-skip
dedupes it).

Config (source.options):
    repos:    list[str]  required — "owner/name" repositories
    include:  list[str]  optional — subset of {pull_requests, issues, releases, readme}
    page_size: int       optional — REST page size (default 100, GitHub max)
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from universal_rag.config import get_settings
from universal_rag.connectors.base import Connector, SourceDocument, SyncCursor

_CURSOR_SAFETY = timedelta(minutes=5)
_DEFAULT_INCLUDE = ("pull_requests", "issues", "releases", "readme")
_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested without any network)
# --------------------------------------------------------------------------- #
def split_repo(repo: str) -> tuple[str, str]:
    owner, _, name = repo.partition("/")
    if not owner or not name:
        raise ValueError(f"repo must be 'owner/name', got '{repo}'")
    return owner, name


def _parse_gh_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def issue_or_pr_to_document(source_id: str, repo: str, item: dict[str, Any]) -> SourceDocument:
    """Map a GitHub issue or pull request (issues-endpoint shape) to a document."""
    number = item.get("number")
    pr = item.get("pull_request")
    is_pr = pr is not None
    kind = "pr" if is_pr else "issue"
    title = item.get("title", "")
    body = item.get("body") or ""

    metadata: dict[str, object] = {
        "doc_type": f"github_{kind}",
        "repo": repo,
        "number": number,
        "state": item.get("state", ""),
        "author": (item.get("user") or {}).get("login", ""),
        "labels": [lbl.get("name", "") for lbl in (item.get("labels") or [])],
        "created": item.get("created_at"),
        "url": item.get("html_url", ""),
    }
    if pr is not None:
        metadata["merged"] = bool(pr.get("merged_at"))

    label = "PR" if is_pr else "Issue"
    return SourceDocument(
        source_id=source_id,
        provider="github",
        external_id=f"{repo}#{kind}-{number}",
        title=f"{repo}#{number} ({label}): {title}",
        content=f"{title}\n\n{body}".strip(),
        url=str(metadata["url"]),
        updated_at=_parse_gh_dt(item.get("updated_at")),
        metadata=metadata,
    )


def release_to_document(source_id: str, repo: str, release: dict[str, Any]) -> SourceDocument:
    tag = release.get("tag_name", "")
    name = release.get("name") or tag
    body = release.get("body") or ""
    published = release.get("published_at") or release.get("created_at")
    metadata: dict[str, object] = {
        "doc_type": "github_release",
        "repo": repo,
        "tag": tag,
        "author": (release.get("author") or {}).get("login", ""),
        "prerelease": bool(release.get("prerelease")),
        "url": release.get("html_url", ""),
    }
    return SourceDocument(
        source_id=source_id,
        provider="github",
        external_id=f"{repo}#release-{release.get('id')}",
        title=f"{repo} release {tag}: {name}".strip(),
        content=f"{name}\n\n{body}".strip(),
        url=str(metadata["url"]),
        updated_at=_parse_gh_dt(published),
        metadata=metadata,
    )


def readme_to_document(source_id: str, repo: str, readme: dict[str, Any]) -> SourceDocument:
    raw = readme.get("content", "")
    if readme.get("encoding") == "base64":
        text = base64.b64decode(raw).decode("utf-8", errors="replace")
    else:
        text = raw
    metadata: dict[str, object] = {
        "doc_type": "github_readme",
        "repo": repo,
        "path": readme.get("path", "README"),
        "url": readme.get("html_url", ""),
    }
    return SourceDocument(
        source_id=source_id,
        provider="github",
        external_id=f"{repo}#readme",
        title=f"{repo} README",
        content=text.strip(),
        url=str(metadata["url"]),
        updated_at=None,  # no reliable timestamp; hash-skip handles dedupe
        metadata=metadata,
    )


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #
def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500  # don't retry 4xx (e.g. 404 readme)
    return False


class GitHubClient:
    """Thin REST client for GitHub / GitHub Enterprise using PAT bearer auth."""

    def __init__(
        self,
        token: str,
        api_url: str = "https://api.github.com",
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.api_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": _ACCEPT,
                "X-GitHub-Api-Version": _API_VERSION,
            },
            timeout=timeout,
            transport=transport,
        )

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        resp = self._client.get(path, params=params or {})
        resp.raise_for_status()
        return resp

    def _paginate(self, path: str, params: dict[str, Any], page_size: int) -> Iterator[dict]:
        page = 1
        while True:
            data = self._get(path, {**params, "per_page": page_size, "page": page}).json()
            if not data:
                break
            yield from data
            if len(data) < page_size:
                break
            page += 1

    def iter_issues(
        self, owner: str, repo: str, since: datetime | None, page_size: int
    ) -> Iterator[dict]:
        params: dict[str, Any] = {"state": "all", "sort": "updated", "direction": "asc"}
        if since is not None:
            params["since"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        yield from self._paginate(f"/repos/{owner}/{repo}/issues", params, page_size)

    def iter_releases(self, owner: str, repo: str, page_size: int) -> Iterator[dict]:
        yield from self._paginate(f"/repos/{owner}/{repo}/releases", {}, page_size)

    def get_readme(self, owner: str, repo: str) -> dict[str, Any] | None:
        try:
            return self._get(f"/repos/{owner}/{repo}/readme").json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    def current_user(self) -> dict[str, Any]:
        return self._get("/user").json()

    def close(self) -> None:
        self._client.close()


# --------------------------------------------------------------------------- #
# Connector
# --------------------------------------------------------------------------- #
class GitHubConnector(Connector):
    provider = "github"

    def _client(self) -> GitHubClient:
        s = get_settings()
        if not s.github_token:
            raise RuntimeError("GitHub not configured: set GITHUB_TOKEN in .env")
        return GitHubClient(s.github_token, s.github_api_url)

    def _repos(self) -> list[str]:
        repos = self.source.opt("repos") or []
        if not repos:
            raise RuntimeError(f"GitHub source '{self.source.id}' has no 'repos' configured")
        return list(repos)

    @staticmethod
    def _since(cursor: SyncCursor | None) -> datetime | None:
        if not cursor or not cursor.value:
            return None
        dt = _parse_gh_dt(cursor.value)
        return dt - _CURSOR_SAFETY if dt else None

    def fetch(self, cursor: SyncCursor | None = None) -> Iterator[SourceDocument]:
        client = self._client()
        since = self._since(cursor)
        include = set(self.source.opt("include") or _DEFAULT_INCLUDE)
        page_size = int(self.source.opt("page_size", 100))
        try:
            for repo in self._repos():
                owner, name = split_repo(repo)

                if include & {"issues", "pull_requests"}:
                    for item in client.iter_issues(owner, name, since, page_size):
                        is_pr = "pull_request" in item
                        if is_pr and "pull_requests" not in include:
                            continue
                        if not is_pr and "issues" not in include:
                            continue
                        yield issue_or_pr_to_document(self.source.id, repo, item)

                if "releases" in include:
                    for rel in client.iter_releases(owner, name, page_size):
                        doc = release_to_document(self.source.id, repo, rel)
                        if since and doc.updated_at and doc.updated_at < since:
                            continue
                        yield doc

                if "readme" in include:
                    readme = client.get_readme(owner, name)
                    if readme:
                        yield readme_to_document(self.source.id, repo, readme)
        finally:
            client.close()

    def healthcheck(self) -> bool:
        try:
            self._client().current_user()
            return True
        except Exception:
            return False
