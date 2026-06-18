from __future__ import annotations

from datetime import datetime

import httpx

from universal_rag.connectors.jira import (
    JiraDataCenterClient,
    build_jql,
    issue_to_document,
)


def test_build_jql_full_scan() -> None:
    jql = build_jql(["APOL"], None, None)
    assert jql == 'project in ("APOL") ORDER BY updated ASC'


def test_build_jql_incremental_with_extra() -> None:
    since = datetime(2024, 3, 1, 9, 30)
    jql = build_jql(["APOL", "OPS"], since, "status != Done")
    assert 'project in ("APOL", "OPS")' in jql
    assert 'updated >= "2024/03/01 09:30"' in jql
    assert "(status != Done)" in jql
    assert jql.endswith("ORDER BY updated ASC")


def test_issue_to_document_maps_fields_url_and_comments() -> None:
    issue = {
        "key": "APOL-42",
        "fields": {
            "summary": "Ship analytics dashboard",
            "description": "Deliver the Q3 dashboard.",
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Story"},
            "priority": {"name": "High"},
            "assignee": {"displayName": "Ada Lovelace"},
            "reporter": {"displayName": "Alan Turing"},
            "labels": ["roadmap", "q3"],
            "components": [{"name": "frontend"}],
            "fixVersions": [{"name": "2.0"}],
            "created": "2024-02-01T08:00:00.000+0000",
            "updated": "2024-03-01T09:30:00.000+0000",
            "resolution": None,
            "comment": {
                "comments": [
                    {"author": {"displayName": "Grace"}, "body": "On deck for next sprint."}
                ]
            },
        },
    }
    doc = issue_to_document("apollo-jira", issue, "https://jira.acme.com/")

    assert doc.external_id == "APOL-42"
    assert doc.provider == "jira"
    assert doc.title == "APOL-42: Ship analytics dashboard"
    assert "Deliver the Q3 dashboard." in doc.content
    assert "Comment by Grace: On deck for next sprint." in doc.content
    assert doc.url == "https://jira.acme.com/browse/APOL-42"
    assert doc.updated_at == datetime.fromisoformat("2024-03-01T09:30:00.000+00:00")
    assert doc.metadata["project"] == "APOL"
    assert doc.metadata["status"] == "In Progress"
    assert doc.metadata["assignee"] == "Ada Lovelace"
    assert doc.metadata["labels"] == ["roadmap", "q3"]
    assert doc.metadata["components"] == ["frontend"]


def test_issue_to_document_can_skip_comments() -> None:
    issue = {
        "key": "APOL-1",
        "fields": {
            "summary": "X",
            "description": "body",
            "comment": {"comments": [{"author": {"displayName": "G"}, "body": "noise"}]},
        },
    }
    doc = issue_to_document("s", issue, "https://j", include_comments=False)
    assert "noise" not in doc.content
    assert doc.content == "X\n\nbody"


def test_client_pagination_and_bearer_auth() -> None:
    seen_auth: list[str] = []
    pages = {
        0: {"issues": [{"key": "A-1"}, {"key": "A-2"}], "startAt": 0, "maxResults": 2, "total": 3},
        2: {"issues": [{"key": "A-3"}], "startAt": 2, "maxResults": 2, "total": 3},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/2/search"
        seen_auth.append(request.headers["Authorization"])
        start = int(request.url.params["startAt"])
        return httpx.Response(200, json=pages[start])

    client = JiraDataCenterClient(
        "https://jira.acme.com", "TESTTOKEN", transport=httpx.MockTransport(handler)
    )
    keys = [i["key"] for i in client.iter_search("project = A", "summary", page_size=2)]

    assert keys == ["A-1", "A-2", "A-3"]
    assert seen_auth == ["Bearer TESTTOKEN", "Bearer TESTTOKEN"]
