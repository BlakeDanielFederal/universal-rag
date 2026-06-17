from __future__ import annotations

from datetime import datetime

import httpx

from universal_rag.connectors.confluence import (
    ConfluenceDataCenterClient,
    build_cql,
    html_to_text,
    page_to_document,
)


def test_build_cql_full_scan() -> None:
    cql = build_cql("APOLLO", None, None)
    assert cql == 'space = "APOLLO" and type = page order by lastmodified asc'


def test_build_cql_incremental_with_labels() -> None:
    since = datetime(2024, 3, 1, 9, 30)
    cql = build_cql("APOLLO", since, ["roadmap", "approved"])
    assert 'space = "APOLLO"' in cql
    assert 'lastmodified >= "2024-03-01 09:30"' in cql
    assert 'label in ("roadmap", "approved")' in cql


def test_html_to_text_strips_storage_markup() -> None:
    html = "<h1>Roadmap</h1><p>Phase <strong>one</strong> ships Q3.</p><p>Phase two.</p>"
    text = html_to_text(html)
    assert "Roadmap" in text
    assert "Phase one ships Q3." in text
    assert "<" not in text
    assert "\n\n\n" not in text  # blank-line runs collapsed


def test_page_to_document_maps_fields_and_url() -> None:
    page = {
        "id": "12345",
        "title": "Apollo Roadmap",
        "type": "page",
        "space": {"key": "APOLLO"},
        "body": {"storage": {"value": "<p>Milestones</p>"}},
        "version": {"number": 7, "when": "2024-03-01T09:30:00.000Z", "by": {"displayName": "Ada"}},
        "_links": {"base": "https://confluence.acme.com", "webui": "/display/APOLLO/Roadmap"},
    }
    doc = page_to_document("apollo-confluence", page, "https://confluence.acme.com")

    assert doc.external_id == "12345"
    assert doc.provider == "confluence"
    assert doc.title == "Apollo Roadmap"
    assert doc.content == "Milestones"
    assert doc.url == "https://confluence.acme.com/display/APOLLO/Roadmap"
    assert doc.updated_at == datetime.fromisoformat("2024-03-01T09:30:00+00:00")
    assert doc.metadata["space"] == "APOLLO"
    assert doc.metadata["version"] == 7
    assert doc.metadata["author"] == "Ada"


def test_client_pagination_and_bearer_auth() -> None:
    seen_auth: list[str] = []
    pages = {
        0: {"results": [{"id": "1"}, {"id": "2"}], "start": 0, "limit": 2, "size": 2},
        2: {"results": [{"id": "3"}], "start": 2, "limit": 2, "size": 1},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/content/search"
        seen_auth.append(request.headers["Authorization"])
        start = int(request.url.params["start"])
        return httpx.Response(200, json=pages[start])

    client = ConfluenceDataCenterClient(
        "https://confluence.acme.com", "TESTTOKEN", transport=httpx.MockTransport(handler)
    )
    ids = [p["id"] for p in client.iter_search("space = X", "body.storage", page_size=2)]

    assert ids == ["1", "2", "3"]  # paged across two requests, then stopped
    assert seen_auth == ["Bearer TESTTOKEN", "Bearer TESTTOKEN"]
