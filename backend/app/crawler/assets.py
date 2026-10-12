"""Find the picture addresses inside a web page.

Pages hide pictures in many places, so we look in all the usual ones:
social-preview tags, video posters, <img> (including lazy-loaded and responsive
versions), <picture>, direct links to image files, and CSS background styles.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

SKIP_EXTENSIONS = (".svg", ".gif", ".ico")  # icons and animations: not worth comparing
IMAGE_LINK_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
LAZY_ATTRIBUTES = ("src", "data-src", "data-original", "data-lazy-src", "data-lazy", "data-img")
SRCSET_ATTRIBUTES = ("srcset", "data-srcset")
MIN_DECLARED_SIDE = 48  # an <img> that says it is tiny is an icon or a tracking pixel
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)


def clean_url(raw: str | None, base: str) -> str | None:
    """Make an address absolute and tidy, or return None if it is not worth fetching."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw or raw.startswith(("data:", "javascript:", "mailto:", "#", "blob:")):
        return None
    try:
        parts = urlsplit(urljoin(base, raw))
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if parts.path.lower().endswith(SKIP_EXTENSIONS):
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _best_from_srcset(value: str | None) -> str | None:
    """From 'a.jpg 320w, b.jpg 800w' pick the largest picture."""
    if not value:
        return None
    best_url, best_size = None, -1.0
    for item in value.split(","):
        pieces = item.strip().split()
        if not pieces:
            continue
        size = 0.0
        if len(pieces) > 1:
            try:
                size = float(pieces[1][:-1])
            except ValueError:
                size = 0.0
        if size >= best_size:
            best_url, best_size = pieces[0], size
    return best_url


def _declared_tiny(tag) -> bool:
    for attribute in ("width", "height"):
        value = (tag.get(attribute) or "").strip().rstrip("px")
        if value.isdigit() and int(value) <= MIN_DECLARED_SIDE:
            return True
    return False


def extract_image_urls(html: str, page_url: str, limit: int) -> list[str]:
    """Picture addresses found in the page, in a sensible order, without repeats."""
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str | None) -> None:
        url = clean_url(raw, page_url)
        if url and url not in seen:
            seen.add(url)
            found.append(url)

    # 1. social-preview pictures, usually the main picture of the page
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or "").lower()
        if key in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"):
            add(tag.get("content"))

    # 2. video posters
    for tag in soup.find_all("video"):
        add(tag.get("poster"))

    # 3. <img> in all its disguises
    for tag in soup.find_all("img"):
        if _declared_tiny(tag):
            continue
        for attribute in SRCSET_ATTRIBUTES:
            add(_best_from_srcset(tag.get(attribute)))
        for attribute in LAZY_ATTRIBUTES:
            add(tag.get(attribute))

    # 4. <picture><source srcset=...>
    for tag in soup.find_all("source"):
        for attribute in SRCSET_ATTRIBUTES:
            add(_best_from_srcset(tag.get(attribute)))

    # 5. links that point straight at an image file
    for tag in soup.find_all("a", href=True):
        if urlsplit(tag["href"]).path.lower().endswith(IMAGE_LINK_EXTENSIONS):
            add(tag["href"])

    # 6. CSS background pictures written inside style="..."
    for tag in soup.find_all(style=True):
        for match in _CSS_URL.finditer(tag["style"]):
            add(match.group(2))

    return found[:limit]