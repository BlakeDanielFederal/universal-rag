"""Website connector — recursive same-domain crawler with main-content extraction.

BFS-crawls from `start_urls`, following links within the allowed domains up to a
depth and page budget, and extracts the main article text (trafilatura, which
strips nav/menus/footers/boilerplate). One document per crawled page.

Each run re-crawls (HTTP has no cheap "changed since"); unchanged pages are
hash-skipped downstream. Deletions are reconciled from the set of URLs the crawl
actually reached this run (`list_external_ids`), so pages that 404 or vanish get
pruned.

Config (source.options):
    start_urls:      list[str]  required — seed URLs
    max_depth:       int        optional — link-follow depth (default 2)
    max_pages:       int        optional — crawl budget per sync (default 200)
    allowed_domains: list[str]  optional — hosts to stay within (default: start hosts)
    respect_robots:  bool       optional — honor robots.txt (default True)
    delay_seconds:   float      optional — politeness delay between requests (default 0.5)
    user_agent:      str        optional — request UA
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterator
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from universal_rag.config.schema import SourceConfig
from universal_rag.connectors.base import Connector, SourceDocument, SyncCursor

_DEFAULT_UA = "universal-rag-crawler/0.1 (+https://github.com/)"


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested without any network)
# --------------------------------------------------------------------------- #
def normalize_url(url: str) -> str:
    """Drop the fragment and any trailing slash (except root) for stable dedup/ids."""
    url, _ = urldefrag(url)
    parts = urlsplit(url)
    path = parts.path.rstrip("/")  # "" for root; "/a/" -> "/a"
    return f"{parts.scheme}://{parts.netloc}{path}" + (f"?{parts.query}" if parts.query else "")


def host_of(url: str) -> str:
    return urlsplit(url).netloc


def extract_links(html: str, base_url: str, allowed_domains: set[str]) -> list[str]:
    """Absolute, normalized http(s) links within the allowed domains."""
    out: list[str] = []
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        absolute = normalize_url(urljoin(base_url, href))
        if urlsplit(absolute).scheme in ("http", "https") and host_of(absolute) in allowed_domains:
            out.append(absolute)
    return out


def html_title(html: str) -> str:
    tag = BeautifulSoup(html, "html.parser").title
    return tag.get_text(strip=True) if tag and tag.string is not None else ""


def extract_main_text(html: str, url: str = "") -> str:
    """Main readable content with boilerplate stripped (trafilatura)."""
    import trafilatura

    text = trafilatura.extract(html, url=url or None, include_comments=False, include_tables=True)
    return (text or "").strip()


def _last_modified(resp: httpx.Response) -> datetime | None:
    value = resp.headers.get("last-modified")
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Connector
# --------------------------------------------------------------------------- #
class WebsiteConnector(Connector):
    provider = "website"

    def __init__(self, source: SourceConfig) -> None:
        super().__init__(source)
        self._seen: set[str] = set()
        self._crawled = False

    def _user_agent(self) -> str:
        return str(self.source.opt("user_agent") or _DEFAULT_UA)

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={"User-Agent": self._user_agent()},
            follow_redirects=True,
            timeout=float(self.source.opt("timeout", 30.0)),
        )

    def _allowed_domains(self, start_urls: list[str]) -> set[str]:
        configured = self.source.opt("allowed_domains")
        if configured:
            return set(configured)
        return {host_of(u) for u in start_urls}

    def _robots_ok(self, client: httpx.Client, cache: dict[str, RobotFileParser], url: str) -> bool:
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        rp = cache.get(key)
        if rp is None:
            rp = RobotFileParser()
            try:
                r = client.get(f"{key}/robots.txt")
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except httpx.HTTPError:
                rp.parse([])  # unreachable robots.txt -> allow
            cache[key] = rp
        return rp.can_fetch(self._user_agent(), url)

    def fetch(self, cursor: SyncCursor | None = None) -> Iterator[SourceDocument]:
        start_urls = [normalize_url(u) for u in (self.source.opt("start_urls") or [])]
        if not start_urls:
            raise RuntimeError(f"Website source '{self.source.id}' has no 'start_urls'")
        max_depth = int(self.source.opt("max_depth", 2))
        max_pages = int(self.source.opt("max_pages", 200))
        respect_robots = bool(self.source.opt("respect_robots", True))
        delay = float(self.source.opt("delay_seconds", 0.5))
        allowed = self._allowed_domains(start_urls)

        self._seen = set()
        self._crawled = False
        visited: set[str] = set()
        robots: dict[str, RobotFileParser] = {}
        queue: deque[tuple[str, int]] = deque((u, 0) for u in start_urls)
        fetched = 0
        client = self._client()
        try:
            while queue and fetched < max_pages:
                url, depth = queue.popleft()
                if url in visited:
                    continue
                visited.add(url)
                if respect_robots and not self._robots_ok(client, robots, url):
                    continue
                try:
                    resp = client.get(url)
                except httpx.HTTPError:
                    continue
                fetched += 1
                if delay:
                    time.sleep(delay)
                if resp.status_code != 200 or "html" not in resp.headers.get("content-type", ""):
                    continue
                html = resp.text

                text = extract_main_text(html, url)
                if text:
                    self._seen.add(url)
                    yield SourceDocument(
                        source_id=self.source.id,
                        provider="website",
                        external_id=url,
                        title=html_title(html) or url,
                        content=text,
                        url=url,
                        updated_at=_last_modified(resp),
                        metadata={"doc_type": "web_page", "domain": host_of(url), "depth": depth},
                    )
                if depth < max_depth:
                    for link in extract_links(html, url, allowed):
                        if link not in visited:
                            queue.append((link, depth + 1))
        finally:
            client.close()
            self._crawled = True

    def list_external_ids(self) -> set[str] | None:
        # Reconcile against the URLs this run actually reached. None before a crawl
        # (and the pipeline's empty-set guard covers a fully-failed crawl).
        return self._seen if self._crawled else None
