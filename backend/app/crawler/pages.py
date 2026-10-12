"""Decide which pages of a website to read.

We cannot read a whole website, so for each one we pick a limited, sensible set of pages:
the homepage, pages the site lists in its sitemap, and links found on the homepage.
Pages whose address contains words like 'video' or 'gallery' come first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import zip_longest
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from app.crawler.fetcher import Fetcher, FetchResult

KEYWORDS = (
    "video", "watch", "clip", "gallery", "album", "photo", "pics", "post",
    "scene", "episode", "mms", "view", "image",
)
NOT_PAGES = (
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".ico", ".mp4", ".webm", ".mov",
    ".avi", ".zip", ".pdf", ".css", ".js", ".json", ".xml", ".gz", ".mp3",
)
MAX_SITEMAPS_READ = 4
MAX_SITEMAP_BYTES = 2_000_000
_LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", re.I | re.S)


@dataclass
class PageDiscovery:
    homepage: FetchResult
    pages: list[str] = field(default_factory=list)  # the homepage first, then the rest


def _bare_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def same_site(base_url: str, url: str) -> bool:
    """True for the same website, including its subdomains (and with or without www)."""
    base, host = _bare_host(base_url), _bare_host(url)
    return bool(base) and (host == base or host.endswith("." + base))


def keyword_score(url: str) -> int:
    path = urlsplit(url).path.lower()
    return sum(1 for word in KEYWORDS if word in path)


def _clean_page_url(raw: str, base: str) -> str | None:
    try:
        parts = urlsplit(urljoin(base, raw.strip()))
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if parts.path.lower().endswith(NOT_PAGES):
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def links_from_html(html: str, page_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for tag in soup.find_all("a", href=True):
        url = _clean_page_url(tag["href"], page_url)
        if url and same_site(page_url, url):
            links.append(url)
    return links


def parse_sitemap(text: str) -> tuple[list[str], list[str]]:
    """Returns (page addresses, nested sitemap addresses). A sitemap is plain XML,
    so we read the <loc> tags with a pattern instead of a full XML parser."""
    locations = [m.strip() for m in _LOC.findall(text)]
    if "<sitemapindex" in text.lower():
        return [], locations
    nested = [loc for loc in locations if urlsplit(loc).path.lower().endswith(".xml")]
    pages = [loc for loc in locations if loc not in nested]
    return pages, nested


def _unique(urls: list[str]) -> list[str]:
    return list(dict.fromkeys(urls))


def _interleave(first: list[str], second: list[str]) -> list[str]:
    """a1, b1, a2, b2, ... so that neither list crowds out the other."""
    return [u for pair in zip_longest(first, second) for u in pair if u]


async def _sitemap_pages(fetcher: Fetcher, site_url: str) -> list[str]:
    queue = await fetcher.sitemaps_for(site_url) or [urljoin(site_url, "/sitemap.xml")]
    pages: list[str] = []
    read = 0
    while queue and read < MAX_SITEMAPS_READ:
        address = queue.pop(0)
        if not same_site(site_url, address) or address.lower().endswith(".gz"):
            continue
        result = await fetcher.fetch(
            address, accept=("text/", "application/xml", "application/xhtml"),
            max_bytes=MAX_SITEMAP_BYTES,
        )
        read += 1
        if not result.ok:
            continue
        found_pages, nested = parse_sitemap(result.body.decode("utf-8", errors="replace"))
        pages.extend(found_pages)
        queue.extend(nested)
    cleaned = (_clean_page_url(p, site_url) for p in pages)
    return [p for p in cleaned if p and same_site(site_url, p)]


async def discover_pages(
    fetcher: Fetcher, start_url: str, max_pages: int, max_html_bytes: int
) -> PageDiscovery:
    """Fetch the homepage and choose up to `max_pages` pages. If the homepage cannot be
    read, `homepage.ok` is False and `pages` is empty: the caller reports why."""
    homepage = await fetcher.fetch(
        start_url, accept=("text/html", "application/xhtml"), max_bytes=max_html_bytes
    )
    if not homepage.ok:
        return PageDiscovery(homepage=homepage)

    site_url = homepage.final_url or start_url  # the homepage may have redirected
    html = homepage.body.decode("utf-8", errors="replace")
    from_links = _unique(links_from_html(html, site_url))
    from_sitemap = _unique(await _sitemap_pages(fetcher, site_url))

    def with_keywords(urls: list[str]) -> list[str]:
        return [u for u in urls if keyword_score(u)]

    def without_keywords(urls: list[str]) -> list[str]:
        return [u for u in urls if not keyword_score(u)]

    ordered = (
        _interleave(with_keywords(from_sitemap), with_keywords(from_links))
        + _interleave(without_keywords(from_links), without_keywords(from_sitemap))
    )
    home = urlunsplit(urlsplit(site_url)._replace(fragment=""))
    pages = [home] + [u for u in _unique(ordered) if u != home]
    return PageDiscovery(homepage=homepage, pages=pages[:max_pages])