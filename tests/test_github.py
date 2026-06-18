from __future__ import annotations

import base64
from datetime import datetime

import httpx
import pytest

from universal_rag.connectors.github import (
    GitHubClient,
    issue_or_pr_to_document,
    readme_to_document,
    release_to_document,
    split_repo,
)


def test_split_repo() -> None:
    assert split_repo("octo/hello") == ("octo", "hello")
    with pytest.raises(ValueError):
        split_repo("no-slash")


def test_issue_maps_to_document() -> None:
    item = {
        "number": 7,
        "title": "Fix login redirect",
        "body": "Users bounce on SSO.",
        "state": "open",
        "user": {"login": "ada"},
        "labels": [{"name": "bug"}, {"name": "auth"}],
        "created_at": "2024-02-01T08:00:00Z",
        "updated_at": "2024-03-01T09:30:00Z",
        "html_url": "https://github.com/octo/web/issues/7",
    }
    doc = issue_or_pr_to_document("apollo-github", "octo/web", item)
    assert doc.external_id == "octo/web#issue-7"
    assert doc.title == "octo/web#7 (Issue): Fix login redirect"
    assert "Users bounce on SSO." in doc.content
    assert doc.metadata["doc_type"] == "github_issue"
    assert doc.metadata["labels"] == ["bug", "auth"]
    assert "merged" not in doc.metadata
    assert doc.updated_at == datetime.fromisoformat("2024-03-01T09:30:00+00:00")


def test_pull_request_is_distinguished_and_merged_flagged() -> None:
    item = {
        "number": 12,
        "title": "Add OAuth2 gateway",
        "body": "Implements token rotation.",
        "state": "closed",
        "user": {"login": "grace"},
        "labels": [],
        "updated_at": "2024-03-02T10:00:00Z",
        "html_url": "https://github.com/octo/web/pull/12",
        "pull_request": {"merged_at": "2024-03-02T09:00:00Z"},
    }
    doc = issue_or_pr_to_document("s", "octo/web", item)
    assert doc.external_id == "octo/web#pr-12"
    assert doc.title == "octo/web#12 (PR): Add OAuth2 gateway"
    assert doc.metadata["doc_type"] == "github_pr"
    assert doc.metadata["merged"] is True


def test_release_maps_to_document() -> None:
    rel = {
        "id": 999,
        "tag_name": "v2.0",
        "name": "Analytics GA",
        "body": "Dashboards now generally available.",
        "author": {"login": "ada"},
        "prerelease": False,
        "published_at": "2024-03-05T12:00:00Z",
        "html_url": "https://github.com/octo/web/releases/tag/v2.0",
    }
    doc = release_to_document("s", "octo/web", rel)
    assert doc.external_id == "octo/web#release-999"
    assert doc.title == "octo/web release v2.0: Analytics GA"
    assert "generally available" in doc.content
    assert doc.metadata["tag"] == "v2.0"
    assert doc.updated_at == datetime.fromisoformat("2024-03-05T12:00:00+00:00")


def test_readme_base64_is_decoded() -> None:
    raw = base64.b64encode(b"# Apollo\n\nThe analytics service.").decode()
    readme = {
        "content": raw,
        "encoding": "base64",
        "path": "README.md",
        "html_url": "https://github.com/octo/web/blob/main/README.md",
    }
    doc = readme_to_document("s", "octo/web", readme)
    assert doc.external_id == "octo/web#readme"
    assert doc.title == "octo/web README"
    assert "The analytics service." in doc.content
    assert doc.metadata["path"] == "README.md"


def test_client_pagination_auth_and_headers() -> None:
    seen: list[tuple[str, str, str]] = []
    pages = {1: [{"number": 1}, {"number": 2}], 2: [{"number": 3}]}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octo/web/issues"
        seen.append(
            (
                request.headers["Authorization"],
                request.headers["Accept"],
                request.url.params["page"],
            )
        )
        return httpx.Response(200, json=pages[int(request.url.params["page"])])

    client = GitHubClient("TESTTOKEN", transport=httpx.MockTransport(handler))
    nums = [i["number"] for i in client.iter_issues("octo", "web", None, page_size=2)]

    assert nums == [1, 2, 3]
    assert seen[0][0] == "Bearer TESTTOKEN"
    assert seen[0][1] == "application/vnd.github+json"
    assert [s[2] for s in seen] == ["1", "2"]  # stopped when page 2 returned < per_page


def test_readme_404_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = GitHubClient("T", transport=httpx.MockTransport(handler))
    assert client.get_readme("octo", "empty") is None
