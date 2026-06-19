from __future__ import annotations

import httpx
import pytest

from universal_rag.config.schema import SourceConfig
from universal_rag.connectors.website import (
    WebsiteConnector,
    extract_links,
    extract_main_text,
    html_title,
    normalize_url,
)


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_normalize_url_strips_fragment_and_trailing_slash() -> None:
    assert normalize_url("https://x.com/a/#sec") == "https://x.com/a"
    assert normalize_url("https://x.com/") == "https://x.com"
    assert normalize_url("https://x.com/a?b=1") == "https://x.com/a?b=1"


def test_extract_links_resolves_and_filters_domain() -> None:
    html = """
    <a href="/docs">rel</a>
    <a href="https://other.com/x">external</a>
    <a href="mailto:a@x.com">mail</a>
    <a href="https://x.com/team/">abs same</a>
    """
    links = extract_links(html, "https://x.com/home", {"x.com"})
    assert set(links) == {"https://x.com/docs", "https://x.com/team"}  # external + mailto dropped


def test_html_title() -> None:
    assert html_title("<html><head><title>Apollo Docs</title></head></html>") == "Apollo Docs"


def test_extract_main_text_strips_boilerplate() -> None:
    html = """
    <html><body>
      <nav>Home About Contact Login Menu Search</nav>
      <article><h1>Apollo Quarterly Review</h1>
      <p>The analytics dashboard shipped in Q3 with revenue visualizations and CSV
      export, and the team completed the authentication service using OAuth2 and
      short-lived access tokens issued by the gateway.</p>
      <p>Next quarter focuses on the reporting API and platform performance work.</p>
      </article>
      <footer>Copyright 2024 Apollo Corp</footer>
    </body></html>
    """
    text = extract_main_text(html, "https://x.com/review")
    assert "analytics dashboard shipped in Q3" in text
    assert "Copyright 2024 Apollo Corp" not in text  # footer boilerplate removed


# --------------------------------------------------------------------------- #
# Crawl logic (MockTransport; extraction monkeypatched for determinism)
# --------------------------------------------------------------------------- #
_PAGES = {
    "/a": "<html><head><title>A</title></head><body>"
    '<a href="/b">b</a><a href="http://other.example/x">x</a><a href="/a">self</a>'
    "<p>alpha</p></body></html>",
    "/b": '<html><head><title>B</title></head><body><a href="/c">c</a><p>beta</p></body></html>',
    "/c": "<html><body><p>gamma</p></body></html>",
}


def _handler(request: httpx.Request) -> httpx.Response:
    body = _PAGES.get(request.url.path)
    if body is None:
        return httpx.Response(404, text="nope")
    return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})


def test_crawl_respects_depth_domain_and_reconcile(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deterministic extraction (trafilatura heuristics are content-size sensitive).
    monkeypatch.setattr(
        "universal_rag.connectors.website.extract_main_text",
        lambda html, url="": f"content of {url}",
    )
    conn = WebsiteConnector(
        SourceConfig(
            id="s",
            provider="website",
            start_urls=["http://site.example/a"],
            max_depth=1,
            respect_robots=False,
            delay_seconds=0,
        )
    )
    monkeypatch.setattr(
        conn, "_client", lambda: httpx.Client(transport=httpx.MockTransport(_handler))
    )

    ids = {d.external_id for d in conn.fetch()}
    # /a (depth 0) -> /b (depth 1); /c is depth 2 > max_depth; other.example off-domain
    assert ids == {"http://site.example/a", "http://site.example/b"}
    assert conn.list_external_ids() == ids  # reconcile set = URLs actually reached
