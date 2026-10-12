import asyncio
import socket
import time

import httpx
import pytest

from app.core.netguard import BlockedByNetworkPolicy, NetGuard, is_public_ip
from app.crawler.domains import load_domains, normalise_domain
from app.crawler.fetcher import Fetcher, FetchStatus

PUBLIC_IP = "93.184.216.34"
HTML = "text/html; charset=utf-8"


def run(coroutine):
    return asyncio.run(coroutine)


def make_guard(hosts: dict[str, list[str]] | None = None) -> NetGuard:
    """A guard that uses a fake phone book instead of real DNS."""
    phone_book = {
        "site.test": [PUBLIC_IP],
        "cdn.test": [PUBLIC_IP],
        "internal.test": ["10.0.0.5"],
        "mixed.test": [PUBLIC_IP, "192.168.1.10"],
    }
    phone_book.update(hosts or {})

    async def resolver(host: str) -> list[str]:
        if host in phone_book:
            return phone_book[host]
        raise socket.gaierror("unknown host")

    return NetGuard(resolver=resolver)


def make_fetcher(handler, **kwargs) -> Fetcher:
    return Fetcher(
        guard=kwargs.pop("guard", make_guard()),
        transport=httpx.MockTransport(handler),
        min_delay=kwargs.pop("min_delay", 0),
        **kwargs,
    )


def page(text: str = "<html>hello</html>", status: int = 200, ctype: str = HTML):
    return httpx.Response(status, text=text, headers={"content-type": ctype})


# ===================================================== network guard

@pytest.mark.parametrize(
    "address",
    ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"],
)
def test_public_addresses_are_allowed(address):
    assert is_public_ip(address)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1",   # local and private networks
        "169.254.169.254",                                       # cloud metadata address
        "100.64.0.1",                                            # carrier-grade NAT
        "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1",
        "::ffff:10.0.0.1",                                       # private address hidden in IPv6
        "not-an-ip",
    ],
)
def test_private_and_special_addresses_are_refused(address):
    assert not is_public_ip(address)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://site.test/file",
        "gopher://site.test/",
        "http://user:pass@site.test/",
        "http://site.test:22/",
        "http://site.test:6379/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://internal.test/",     # a name that points to a private address
        "http://mixed.test/",        # one good address and one private address: still refused
    ],
)
def test_guard_refuses_unsafe_urls(url):
    with pytest.raises(BlockedByNetworkPolicy):
        run(make_guard().check(url))


@pytest.mark.parametrize("url", ["https://site.test/a", "http://site.test:8080/", "https://93.184.216.34/"])
def test_guard_allows_normal_urls(url):
    run(make_guard().check(url))


def test_unknown_host_is_an_os_error_not_a_policy_block():
    with pytest.raises(OSError):
        run(make_guard().check("https://nowhere.test/"))


def test_guard_remembers_answers_for_a_while():
    calls = []

    async def resolver(host):
        calls.append(host)
        return [PUBLIC_IP]

    guard = NetGuard(resolver=resolver)

    async def twice():
        await guard.check("https://site.test/1")
        await guard.check("https://site.test/2")

    run(twice())
    assert calls == ["site.test"]


# ===================================================== fetcher: normal outcomes

def test_successful_fetch_returns_body_and_type():
    async def go():
        async with make_fetcher(lambda r: page("<html>ok</html>")) as f:
            return await f.fetch("https://site.test/", accept=("text/html",), max_bytes=10_000)

    result = run(go())
    assert result.ok and result.body == b"<html>ok</html>"
    assert result.content_type == "text/html"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (404, FetchStatus.not_found), (410, FetchStatus.not_found),
        (403, FetchStatus.blocked), (429, FetchStatus.blocked), (401, FetchStatus.blocked),
        (500, FetchStatus.unreachable), (503, FetchStatus.unreachable),
    ],
)
def test_http_errors_are_classified(code, expected):
    async def go():
        async with make_fetcher(lambda r: httpx.Response(code), ) as f:
            return await f.fetch("https://site.test/x", max_bytes=1000, check_robots=False)

    assert run(go()).status == expected


def test_timeout_is_unreachable_not_an_exception():
    def handler(request):
        raise httpx.ConnectTimeout("slow")

    async def go():
        async with make_fetcher(handler) as f:
            return await f.fetch("https://site.test/", max_bytes=1000, check_robots=False)

    result = run(go())
    assert result.status == FetchStatus.unreachable and result.detail == "timed out"


def test_unknown_host_is_unreachable():
    async def go():
        async with make_fetcher(lambda r: page()) as f:
            return await f.fetch("https://nowhere.test/", max_bytes=1000, check_robots=False)

    assert run(go()).status == FetchStatus.unreachable


def test_wrong_content_type_is_unsupported():
    async def go():
        async with make_fetcher(lambda r: page("png", ctype="image/png")) as f:
            return await f.fetch("https://site.test/", accept=("text/html",), max_bytes=1000, check_robots=False)

    assert run(go()).status == FetchStatus.unsupported


def test_big_download_is_stopped():
    big = "x" * 50_000

    async def go():
        async with make_fetcher(lambda r: page(big)) as f:
            return await f.fetch("https://site.test/", max_bytes=1000, check_robots=False)

    assert run(go()).status == FetchStatus.too_large


def test_download_with_lying_size_header_is_still_stopped():
    def handler(request):
        # a streamed body that is bigger than the limit, with no honest Content-Length
        async def body():
            for _ in range(100):
                yield b"x" * 1000
        return httpx.Response(200, headers={"content-type": HTML}, content=body())

    async def go():
        async with make_fetcher(handler) as f:
            return await f.fetch("https://site.test/", max_bytes=5000, check_robots=False)

    assert run(go()).status == FetchStatus.too_large


def test_requests_identify_the_crawler_and_send_the_referer():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        seen["referer"] = request.headers.get("referer")
        return page()

    async def go():
        async with make_fetcher(handler, user_agent="TraceveilBot/0.1 (+me@example.org)") as f:
            await f.fetch("https://site.test/", max_bytes=1000, check_robots=False,
                          referer="https://site.test/list")

    run(go())
    assert seen["ua"] == "TraceveilBot/0.1 (+me@example.org)"
    assert seen["referer"] == "https://site.test/list"


# ===================================================== fetcher: safety

def test_redirect_to_an_internal_address_is_blocked():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "site.test":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return page("SECRET")  # must never be reached

    async def go():
        async with make_fetcher(handler) as f:
            return await f.fetch("https://site.test/go", max_bytes=1000, check_robots=False)

    result = run(go())
    assert result.status == FetchStatus.blocked
    assert "network policy" in result.detail
    assert calls == ["https://site.test/go"]  # the internal address was never contacted


def test_redirect_to_a_name_that_points_inside_is_blocked():
    def handler(request):
        if request.url.host == "site.test":
            return httpx.Response(302, headers={"location": "http://internal.test/admin"})
        return page("SECRET")

    async def go():
        async with make_fetcher(handler) as f:
            return await f.fetch("https://site.test/go", max_bytes=1000, check_robots=False)

    assert run(go()).status == FetchStatus.blocked


def test_normal_redirect_is_followed():
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://cdn.test/new"})
        return page("moved")

    async def go():
        async with make_fetcher(handler) as f:
            return await f.fetch("https://site.test/old", max_bytes=1000, check_robots=False)

    result = run(go())
    assert result.ok and result.final_url == "https://cdn.test/new"


def test_redirect_loop_is_unreachable():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://site.test/again"})

    async def go():
        async with make_fetcher(handler) as f:
            return await f.fetch("https://site.test/", max_bytes=1000, check_robots=False)

    assert run(go()).status == FetchStatus.unreachable


def test_address_that_resolves_inside_is_blocked_before_any_request():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return page()

    async def go():
        async with make_fetcher(handler) as f:
            return await f.fetch("http://internal.test/", max_bytes=1000)

    assert run(go()).status == FetchStatus.blocked
    assert calls == []


# ===================================================== fetcher: robots.txt

ROBOTS = "User-agent: *\nDisallow: /private\nSitemap: https://site.test/sitemap.xml\n"


def robots_site(calls: list[str]):
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return page(ROBOTS, ctype="text/plain")
        return page("<html>page</html>")
    return handler


def test_robots_disallowed_page_is_not_fetched():
    calls: list[str] = []

    async def go():
        async with make_fetcher(robots_site(calls)) as f:
            return await f.fetch("https://site.test/private/secret", max_bytes=1000)

    result = run(go())
    assert result.status == FetchStatus.blocked and "robots.txt" in result.detail
    assert "/private/secret" not in calls


def test_robots_allowed_page_is_fetched_and_robots_is_read_once():
    calls: list[str] = []

    async def go():
        async with make_fetcher(robots_site(calls)) as f:
            first = await f.fetch("https://site.test/public/a", max_bytes=1000)
            second = await f.fetch("https://site.test/public/b", max_bytes=1000)
            return first, second

    first, second = run(go())
    assert first.ok and second.ok
    assert calls.count("/robots.txt") == 1


def test_sitemaps_are_taken_from_robots():
    async def go():
        async with make_fetcher(robots_site([])) as f:
            return await f.sitemaps_for("https://site.test/anything")

    assert run(go()) == ["https://site.test/sitemap.xml"]


def test_missing_robots_means_no_restrictions():
    def handler(request):
        return httpx.Response(404) if request.url.path == "/robots.txt" else page()

    async def go():
        async with make_fetcher(handler) as f:
            return await f.fetch("https://site.test/anything", max_bytes=1000)

    assert run(go()).ok


def test_broken_robots_means_we_do_not_crawl():
    def handler(request):
        return httpx.Response(500) if request.url.path == "/robots.txt" else page()

    async def go():
        async with make_fetcher(handler) as f:
            return await f.fetch("https://site.test/anything", max_bytes=1000)

    result = run(go())
    assert result.status == FetchStatus.unreachable and "robots.txt could not be read" in result.detail


# ===================================================== fetcher: politeness

def test_requests_to_the_same_host_are_spaced_out():
    async def go():
        async with make_fetcher(lambda r: page(), min_delay=0.1) as f:
            start = time.monotonic()
            await asyncio.gather(*(
                f.fetch(f"https://site.test/{i}", max_bytes=1000, check_robots=False) for i in range(4)
            ))
            return time.monotonic() - start

    assert run(go()) >= 0.28  # 4 requests, 3 gaps of 0.1 s


def test_different_hosts_do_not_wait_for_each_other():
    async def go():
        async with make_fetcher(lambda r: page(), min_delay=0.3) as f:
            start = time.monotonic()
            await asyncio.gather(
                f.fetch("https://site.test/", max_bytes=1000, check_robots=False),
                f.fetch("https://cdn.test/", max_bytes=1000, check_robots=False),
            )
            return time.monotonic() - start

    assert run(go()) < 0.25


# ===================================================== domain list

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.org", "example.org"),
        ("  EXAMPLE.org  ", "example.org"),
        ("https://Example.org/some/page?x=1", "example.org"),
        ("www.example.org.", "www.example.org"),
    ],
)
def test_domains_are_cleaned_up(raw, expected):
    assert normalise_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "localhost", "93.184.216.34", "example.org:8080", "user@example.org",
     "exa mple.org", "-bad.example.org", "http://", "a..b"],
)
def test_bad_domains_are_rejected(raw):
    with pytest.raises(ValueError):
        normalise_domain(raw)


def test_loading_the_csv_skips_comments_and_reports_bad_rows(tmp_path):
    path = tmp_path / "domains.csv"
    path.write_text(
        "\ufeff# comment line\n"
        "domain,category,media_types,notes,last_checked\n"
        "one.example,tube,video,,2026-10-01\n"
        "HTTPS://Two.example/path,gallery,,a note,\n"
        "one.example,tube,video,,2026-10-01\n"
        "10.0.0.1,bad,image,,\n",
        encoding="utf-8",
    )
    entries, problems = load_domains(path)
    assert [e.domain for e in entries] == ["one.example", "two.example"]
    assert entries[0].media_types == ("video",)
    assert entries[1].media_types == ("image", "video")  # default when blank
    assert entries[1].notes == "a note"
    assert len(problems) == 2
    assert any("duplicate" in p for p in problems)
    assert any("IP addresses" in p for p in problems)


def test_the_shipped_domain_file_loads():
    entries, problems = load_domains()
    assert entries and not problems



# ===================================================== development switch (localhost only)

def loopback_guard() -> NetGuard:
    async def resolver(host: str) -> list[str]:
        return {"localhost": ["127.0.0.1", "::1"], "internal.test": ["10.0.0.5"]}[host]

    return NetGuard(resolver=resolver, allow_loopback=True)


@pytest.mark.parametrize(
    "url",
    ["http://localhost:8001/", "http://127.0.0.1:9000/page", "http://[::1]:8001/"],
)
def test_development_switch_allows_this_computer(url):
    run(loopback_guard().check(url))


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5:8001/",                     # another machine on the network
        "http://192.168.1.1/",                       # the home router
        "http://internal.test/",                     # a name that points inside
        "http://localhost:8001@evil.test/",          # tricks using a user name
        "file:///etc/passwd",
    ],
)
def test_development_switch_still_blocks_everything_else(url):
    with pytest.raises(BlockedByNetworkPolicy):
        run(loopback_guard().check(url))


def test_odd_ports_are_still_blocked_for_public_hosts_in_development():
    with pytest.raises(BlockedByNetworkPolicy):
        run(NetGuard(resolver=make_guard()._resolver, allow_loopback=True).check("http://site.test:22/"))


def test_the_switch_is_off_by_default():
    with pytest.raises(BlockedByNetworkPolicy):
        run(make_guard().check("http://127.0.0.1:8001/"))


def test_guard_relaxations_are_refused_outside_development(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "a-long-random-secret-for-this-test-only-123456")
    monkeypatch.setenv("NETGUARD_ALLOW_LOOPBACK", "true")
    with pytest.raises(ValueError):
        Settings()