"""Find the video file(s) a web page offers.

A matching thumbnail only proves a PICTURE on the page looks like one of our frames. To find
out whether the page's video really is ours, we need the video itself, so we look for it
in the places pages normally announce it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

DIRECT_EXTENSIONS = (".mp4", ".webm", ".mov", ".m4v")
STREAM_EXTENSIONS = (".m3u8", ".mpd")


@dataclass(frozen=True)
class VideoRef:
    url: str
    kind: str  # direct: a plain file we can sample | stream: playlist | embed: another site's player | unknown

    def as_dict(self) -> dict:
        return {"url": self.url, "type": self.kind}


def _clean(raw: str | None, base: str) -> str | None:
    if not raw or not raw.strip():
        return None
    try:
        parts = urlsplit(urljoin(base, raw.strip()))
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _kind_from_address(url: str, default: str = "unknown") -> str:
    path = urlsplit(url).path.lower()
    if path.endswith(DIRECT_EXTENSIONS):
        return "direct"
    if path.endswith(STREAM_EXTENSIONS):
        return "stream"
    return default


def _json_ld_videos(soup: BeautifulSoup):
    """VideoObject entries in <script type="application/ld+json"> blocks."""
    def walk(node):
        if isinstance(node, list):
            for item in node:
                yield from walk(item)
        elif isinstance(node, dict):
            kinds = node.get("@type")
            kinds = kinds if isinstance(kinds, list) else [kinds]
            if "VideoObject" in kinds:
                yield node
            for value in node.values():
                if isinstance(value, (list, dict)):
                    yield from walk(value)

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        yield from walk(data)


def find_video_urls(html: str, page_url: str, limit: int = 6) -> list[VideoRef]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, VideoRef] = {}

    def add(raw: str | None, kind: str | None = None, default: str = "unknown") -> None:
        url = _clean(raw, page_url)
        if url and url not in found:
            found[url] = VideoRef(url, kind or _kind_from_address(url, default))

    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or "").lower()
        if key in ("og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"):
            add(tag.get("content"))
    for tag in soup.find_all("video"):
        add(tag.get("src"))
    for tag in soup.find_all("source"):
        declared = (tag.get("type") or "").lower()
        if declared.startswith("video/") or declared in ("application/x-mpegurl", "application/vnd.apple.mpegurl"):
            add(tag.get("src"))
    for node in _json_ld_videos(soup):
        add(node.get("contentUrl"))
        add(node.get("embedUrl"), kind="embed")
    for tag in soup.find_all("iframe"):
        add(tag.get("src") or tag.get("data-src"), kind="embed")

    order = {"direct": 0, "stream": 1, "unknown": 2, "embed": 3}
    return sorted(found.values(), key=lambda ref: order[ref.kind])[:limit]
