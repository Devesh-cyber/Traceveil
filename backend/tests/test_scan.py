import asyncio
import socket

import httpx
import pytest

from app.core.netguard import NetGuard
from app.crawler.assets import extract_image_urls
from app.crawler.domains import DomainEntry
from app.crawler.fetcher import Fetcher
from app.crawler import runner as runner_module
from app.crawler.pages import discover_pages, keyword_score, parse_sitemap, same_site
from app.crawler.runner import ScanLimits, ScanProgress, run_scan
from app.matching.fingerprint import build_reference
from app.models.domain_result import DomainStatus
from app.models.match import MatchKind
from tests.factories import make_photo, to_bytes

PUBLIC_IP = "93.184.216.34"
MY_PHOTO = make_photo(1)
REFERENCE = build_reference(MY_PHOTO)
COPY_JPEG = to_bytes(MY_PHOTO.resize((240, 180)), "JPEG", quality=85)    # a thumbnail copy
MIRROR_JPEG = to_bytes(MY_PHOTO.transpose(0), "JPEG", quality=85)        # mirrored copy
OTHER_JPEG = to_bytes(make_photo(50), "JPEG", quality=85)                # unrelated picture
OTHER_JPEG_2 = to_bytes(make_photo(51), "JPEG", quality=85)


def run(coroutine):
    return asyncio.run(coroutine)


class FakeWeb:
    """A pretend internet: sites made of pages we define, plus a log of every request."""

    def __init__(self):
        self.sites: dict[str, dict[str, tuple[bytes, str, int]]] = {}
        self.broken_hosts: dict[str, Exception] = {}
        self.calls: list[str] = []

    def add(self, host, path, body, ctype="text/html", status=200):
        data = body.encode() if isinstance(body, str) else body
        self.sites.setdefault(host, {})[path] = (data, ctype, status)

    def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        self.calls.append(f"{host}{path}")
        if host in self.broken_hosts:
            raise self.broken_hosts[host]
        entry = self.sites.get(host, {}).get(path)
        if entry is None:
            return httpx.Response(404)
        data, ctype, status = entry
        return httpx.Response(status, content=data, headers={"content-type": ctype})


def make_fetcher(web: FakeWeb) -> Fetcher:
    async def resolver(host: str) -> list[str]:
        if host.endswith(".test"):
            return [PUBLIC_IP]
        raise socket.gaierror("unknown host")

    return Fetcher(guard=NetGuard(resolver=resolver), transport=httpx.MockTransport(web.handler), min_delay=0)


def scan(web: FakeWeb, hosts: list[str], **limit_overrides):
    limits = ScanLimits(**{"parallel_domains": 1, **limit_overrides})
    progress = ScanProgress()
    matches, finished = [], []

    async def on_match(found):
        matches.append(found)

    async def on_site_done(outcome):
        finished.append(outcome.domain)

    async def go():
        async with make_fetcher(web) as fetcher:
            return await run_scan(
                [DomainEntry(domain=h) for h in hosts], REFERENCE, fetcher, limits,
                progress=progress, on_match=on_match, on_site_done=on_site_done,
            )

    summary = run(go())
    return summary, progress, matches, finished


def basic_site(web: FakeWeb, host: str, images: dict[str, bytes] | None = None):
    """A site with a homepage, two 'video' pages and an 'about' page."""
    images = images or {}
    web.add(host, "/", '<a href="/video/1">one</a><a href="/video/2">two</a><a href="/about">about</a>')
    web.add(host, "/video/1", '<img src="/img/copy.jpg">')
    web.add(host, "/video/2", '<img src="/img/other.jpg">')
    web.add(host, "/about", "<p>no pictures here</p>")
    web.add(host, "/img/copy.jpg", images.get("copy", COPY_JPEG), "image/jpeg")
    web.add(host, "/img/other.jpg", images.get("other", OTHER_JPEG), "image/jpeg")


# ============================================================ finding pictures in a page

PAGE = """
<html><head>
  <meta property="og:image" content="/social.jpg">
  <meta name="twitter:image" content="https://cdn.test/tw.jpg">
</head><body>
  <video poster="/poster.jpg"></video>
  <img src="/plain.jpg">
  <img src="/lazy-placeholder.gif" data-src="/lazy.jpg">
  <img data-original="/orig.jpg" src="data:image/png;base64,AAAA">
  <img srcset="/s-320.jpg 320w, /s-800.jpg 800w, /s-480.jpg 480w">
  <picture><source srcset="/pic-1x.jpg 1x, /pic-2x.jpg 2x"><img src="/pic-fallback.jpg"></picture>
  <img src="/pixel.jpg" width="1" height="1">
  <img src="/icon.svg"><img src="/anim.gif"><img src="/favicon.ico">
  <a href="/files/full.jpeg">full size</a><a href="/files/page.html">page</a>
  <div style="background-image: url('/bg.jpg'); color: red"></div>
  <img src="/plain.jpg"><img src="javascript:void(0)"><img src="">
</body></html>
"""


def test_pictures_are_found_in_all_the_usual_places():
    urls = extract_image_urls(PAGE, "https://site.test/list/page", limit=100)
    expected = {
        "https://site.test/social.jpg", "https://cdn.test/tw.jpg", "https://site.test/poster.jpg",
        "https://site.test/plain.jpg", "https://site.test/lazy.jpg", "https://site.test/orig.jpg",
        "https://site.test/s-800.jpg", "https://site.test/pic-2x.jpg", "https://site.test/pic-fallback.jpg",
        "https://site.test/files/full.jpeg", "https://site.test/bg.jpg",
    }
    assert expected <= set(urls)


def test_icons_tiny_pictures_and_junk_are_ignored():
    urls = extract_image_urls(PAGE, "https://site.test/", limit=100)
    joined = " ".join(urls)
    for unwanted in ("pixel.jpg", "icon.svg", "anim.gif", "favicon.ico", "data:", "javascript", "page.html"):
        assert unwanted not in joined


def test_each_picture_is_listed_once_social_preview_first_and_limit_applies():
    urls = extract_image_urls(PAGE, "https://site.test/", limit=100)
    assert len(urls) == len(set(urls))
    assert urls[0] == "https://site.test/social.jpg"
    assert len(extract_image_urls(PAGE, "https://site.test/", limit=3)) == 3


def test_relative_addresses_are_resolved_against_the_page():
    urls = extract_image_urls('<img src="../img/a.jpg"><img src="b.jpg">', "https://site.test/x/y/page", 10)
    assert urls == ["https://site.test/x/img/a.jpg", "https://site.test/x/y/b.jpg"]


# ============================================================ choosing pages

def test_keyword_pages_and_sites_are_recognised():
    assert keyword_score("https://a.test/video/12") == 1
    assert keyword_score("https://a.test/about") == 0
    assert same_site("https://a.test/", "https://www.a.test/x")
    assert same_site("https://www.a.test/", "https://cdn.a.test/x")
    assert not same_site("https://a.test/", "https://evil-a.test/x")
    assert not same_site("https://a.test/", "https://a.test.evil.example/x")


def test_sitemap_parsing_handles_pages_indexes_and_cdata():
    pages, nested = parse_sitemap(
        '<urlset><url><loc>https://a.test/video/1</loc></url>'
        '<url><loc><![CDATA[ https://a.test/video/2 ]]></loc></url></urlset>'
    )
    assert pages == ["https://a.test/video/1", "https://a.test/video/2"] and nested == []

    pages, nested = parse_sitemap(
        "<sitemapindex><sitemap><loc>https://a.test/sitemap-1.xml</loc></sitemap></sitemapindex>"
    )
    assert pages == [] and nested == ["https://a.test/sitemap-1.xml"]


def discover(web, host="a.test", max_pages=15):
    async def go():
        async with make_fetcher(web) as fetcher:
            return await discover_pages(fetcher, f"https://{host}/", max_pages, 1_000_000)
    return run(go())


def test_homepage_comes_first_then_keyword_pages_and_other_sites_are_ignored():
    web = FakeWeb()
    web.add("a.test", "/", (
        '<a href="/about">about</a><a href="/video/1">v1</a><a href="/contact">c</a>'
        '<a href="/gallery/2">g2</a><a href="https://other.test/video/9">elsewhere</a>'
        '<a href="/video/1#comments">dup</a><a href="/photo.jpg">image</a>'
    ))
    result = discover(web)
    assert result.homepage.ok
    assert result.pages[0] == "https://a.test/"
    assert result.pages[1:3] == ["https://a.test/video/1", "https://a.test/gallery/2"]
    assert "https://other.test/video/9" not in result.pages
    assert "https://a.test/photo.jpg" not in result.pages
    assert len(result.pages) == len(set(result.pages))


def test_pages_come_from_the_sitemap_too_including_nested_ones():
    web = FakeWeb()
    web.add("a.test", "/", '<a href="/video/1">v1</a>')
    web.add("a.test", "/robots.txt", "User-agent: *\nSitemap: https://a.test/index.xml\n", "text/plain")
    web.add("a.test", "/index.xml", "<sitemapindex><sitemap><loc>https://a.test/s1.xml</loc></sitemap></sitemapindex>", "application/xml")
    web.add("a.test", "/s1.xml", "<urlset><url><loc>https://a.test/video/100</loc></url><url><loc>https://other.test/video/7</loc></url></urlset>", "application/xml")
    result = discover(web)
    assert "https://a.test/video/100" in result.pages
    assert "https://other.test/video/7" not in result.pages


def test_sitemap_xml_is_tried_when_robots_does_not_mention_one():
    web = FakeWeb()
    web.add("a.test", "/", "<p>hi</p>")
    web.add("a.test", "/sitemap.xml", "<urlset><url><loc>https://a.test/video/5</loc></url></urlset>", "application/xml")
    assert "https://a.test/video/5" in discover(web).pages


def test_page_limit_is_respected():
    web = FakeWeb()
    web.add("a.test", "/", "".join(f'<a href="/video/{i}">x</a>' for i in range(50)))
    assert len(discover(web, max_pages=10).pages) == 10


def test_unreadable_homepage_is_reported_not_raised():
    web = FakeWeb()
    web.add("a.test", "/", "denied", status=403)
    result = discover(web)
    assert not result.homepage.ok and result.pages == []


# ============================================================ running a scan

def test_copies_are_found_and_unrelated_pictures_are_not():
    web = FakeWeb()
    basic_site(web, "good.test")
    summary, progress, matches, _ = scan(web, ["good.test"])

    outcome = summary.outcomes[0]
    assert outcome.status == DomainStatus.checked
    assert len(outcome.matches) == 1
    found = outcome.matches[0]
    assert found.image_url == "https://good.test/img/copy.jpg"
    assert found.source_page_url == "https://good.test/video/1"
    assert found.decision.kind == MatchKind.exact
    assert outcome.pages_scanned == 4  # homepage + 3 links
    assert outcome.images_compared == 2
    assert matches == outcome.matches  # the callback saw the same match
    assert (progress.pages_scanned, progress.images_compared, progress.matches_found) == (4, 2, 1)


def test_mirrored_copy_is_reported_with_its_variant():
    web = FakeWeb()
    basic_site(web, "good.test", images={"copy": MIRROR_JPEG})
    summary, *_ = scan(web, ["good.test"])
    found = summary.outcomes[0].matches[0]
    assert found.decision.kind == MatchKind.near_duplicate and found.decision.variant == "mirrored"


def test_the_same_picture_on_many_pages_is_downloaded_once():
    web = FakeWeb()
    web.add("many.test", "/", "".join(f'<a href="/video/{i}">x</a>' for i in range(1, 6)))
    for i in range(1, 6):
        web.add("many.test", f"/video/{i}", '<img src="/img/shared.jpg">')
    web.add("many.test", "/img/shared.jpg", OTHER_JPEG, "image/jpeg")
    scan(web, ["many.test"])
    assert web.calls.count("many.test/img/shared.jpg") == 1


def test_pages_forbidden_by_robots_are_never_requested():
    web = FakeWeb()
    web.add("r.test", "/", '<a href="/private/video/1">hidden</a><a href="/video/2">open</a>')
    web.add("r.test", "/robots.txt", "User-agent: *\nDisallow: /private/\n", "text/plain")
    web.add("r.test", "/private/video/1", '<img src="/img/copy.jpg">')
    web.add("r.test", "/video/2", '<img src="/img/other.jpg">')
    web.add("r.test", "/img/copy.jpg", COPY_JPEG, "image/jpeg")
    web.add("r.test", "/img/other.jpg", OTHER_JPEG, "image/jpeg")
    summary, *_ = scan(web, ["r.test"])
    assert summary.outcomes[0].matches == []
    assert "r.test/private/video/1" not in web.calls


def test_every_kind_of_failure_gets_its_own_honest_status():
    web = FakeWeb()
    web.add("denied.test", "/", "no", status=403)
    web.broken_hosts["down.test"] = httpx.ConnectError("refused")
    web.add("gone.test", "/", "x", status=404)
    web.add("empty.test", "/", '<a href="/video/1">v</a>')
    web.add("empty.test", "/video/1", "<p>text only</p>")
    web.add("hotlink.test", "/", '<img src="/img/a.jpg">')
    web.add("hotlink.test", "/img/a.jpg", "forbidden", "text/plain", status=403)
    basic_site(web, "good.test")

    summary, *_ = scan(web, ["denied.test", "down.test", "gone.test", "empty.test", "hotlink.test", "good.test"])
    status = {o.domain: o.status for o in summary.outcomes}
    assert status == {
        "denied.test": DomainStatus.blocked,
        "down.test": DomainStatus.unreachable,
        "gone.test": DomainStatus.unreachable,
        "empty.test": DomainStatus.no_images,
        "hotlink.test": DomainStatus.blocked,
        "good.test": DomainStatus.checked,
    }
    assert summary.coverage() == {"checked": 1, "no_images": 1, "blocked": 2, "unreachable": 2, "skipped": 0}
    assert all(o.detail for o in summary.outcomes if o.status != DomainStatus.checked)


def test_a_scan_that_hits_the_image_limit_says_so_and_skips_the_rest():
    web = FakeWeb()
    web.add("big.test", "/", "".join(f'<img src="/img/{i}.jpg">' for i in range(6)))
    for i in range(6):
        web.add("big.test", f"/img/{i}.jpg", OTHER_JPEG if i % 2 else OTHER_JPEG_2, "image/jpeg")
    basic_site(web, "later.test")

    summary, progress, *_ = scan(web, ["big.test", "later.test"], max_total_images=3)
    assert summary.truncated and summary.truncation_reason == "image limit reached"
    big, later = summary.outcomes
    assert big.status == DomainStatus.checked and "stopped early" in big.detail
    assert later.status == DomainStatus.skipped
    assert progress.images_compared == 3  # exactly the 3 allowed; the 4th tripped the limit


def test_a_scan_that_is_out_of_time_skips_every_site():
    web = FakeWeb()
    basic_site(web, "a.test")
    summary, *_ = scan(web, ["a.test"], max_seconds=0)
    assert summary.truncated and summary.truncation_reason == "time limit reached"
    assert summary.outcomes[0].status == DomainStatus.skipped


def test_one_site_crashing_does_not_stop_the_others(monkeypatch):
    web = FakeWeb()
    basic_site(web, "good.test")
    basic_site(web, "crash.test")
    original = runner_module.discover_pages

    async def flaky(fetcher, start_url, *args):
        if "crash.test" in start_url:
            raise RuntimeError("boom")
        return await original(fetcher, start_url, *args)

    monkeypatch.setattr(runner_module, "discover_pages", flaky)
    summary, progress, *_ = scan(web, ["crash.test", "good.test"])
    crashed, good = summary.outcomes
    assert crashed.status == DomainStatus.unreachable and "internal error" in crashed.detail
    assert good.status == DomainStatus.checked and len(good.matches) == 1
    assert progress.sites_done == 2


def test_callbacks_and_progress_cover_every_site():
    web = FakeWeb()
    basic_site(web, "good.test")
    web.add("denied.test", "/", "no", status=403)
    summary, progress, matches, finished = scan(web, ["good.test", "denied.test"])
    assert sorted(finished) == ["denied.test", "good.test"]
    assert progress.sites_total == 2 and progress.sites_done == 2
    assert len(matches) == 1


def test_unreadable_files_do_not_break_the_scan():
    web = FakeWeb()
    web.add("odd.test", "/", '<img src="/a.jpg"><img src="/b.jpg">')
    web.add("odd.test", "/a.jpg", b"this is not really an image", "image/jpeg")
    web.add("odd.test", "/b.jpg", OTHER_JPEG, "image/jpeg")
    summary, *_ = scan(web, ["odd.test"])
    assert summary.outcomes[0].status == DomainStatus.checked
    assert summary.outcomes[0].images_compared == 2