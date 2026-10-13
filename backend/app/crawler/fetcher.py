"""The only place in the project that talks to other websites.

Everything the crawler downloads goes through `Fetcher.fetch`, which:
  * checks every request (and every redirect) with the network guard
  * identifies itself honestly and obeys robots.txt
  * waits between requests to the same host
  * limits how many requests run at once, how long they take and how big they may be
  * never raises for ordinary website problems: it returns a FetchResult whose
    `status` says what happened, so "could not check" is never mistaken for "no match"
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.core.config import get_settings
from app.core.netguard import BlockedByNetworkPolicy, NetGuard

USER_AGENT_TOKEN = "TraceveilBot"
ROBOTS_TTL_SECONDS = 3600
ROBOTS_MAX_BYTES = 500_000
MAX_REDIRECTS = 5


class FetchStatus(str, Enum):
    ok = "ok"
    blocked = "blocked"          # refused: our safety rules, robots.txt, or HTTP 401/403/429/451
    not_found = "not_found"      # HTTP 404 / 410
    unreachable = "unreachable"  # timeout, DNS or connection failure, server errors
    too_large = "too_large"
    unsupported = "unsupported"  # wrong kind of content (e.g. a video when we wanted a page)


@dataclass
class FetchResult:
    status: FetchStatus
    url: str
    final_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    body: bytes = b""
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == FetchStatus.ok


@dataclass
class _Robots:
    parser: RobotFileParser | None
    sitemaps: list[str] = field(default_factory=list)
    deny_all: bool = False
    unreachable: bool = False  # robots.txt could not be read because the site is failing
    detail: str | None = None
    fetched_at: float = 0.0
    

    def allows(self, url: str) -> bool:
        if self.deny_all:
            return False
        return True if self.parser is None else self.parser.can_fetch(USER_AGENT_TOKEN, url)


class _HostThrottle:
    """Makes sure two requests to the same host are at least `min_delay` seconds apart."""

    def __init__(self, min_delay: float) -> None:
        self._min_delay = min_delay
        self._locks: dict[str, asyncio.Lock] = {}
        self._next_allowed: dict[str, float] = {}

    async def wait(self, host: str) -> None:
        if self._min_delay <= 0:
            return
        loop = asyncio.get_running_loop()
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            delay = self._next_allowed.get(host, 0.0) - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_allowed[host] = loop.time() + self._min_delay


def _classify_http_status(code: int) -> tuple[FetchStatus, str] | None:
    """Turn an unsuccessful HTTP status into our status, or None if it is a success."""
    if 200 <= code < 300:
        return None
    if code in (401, 403, 429, 451):
        return FetchStatus.blocked, f"HTTP {code}"
    if code in (404, 410):
        return FetchStatus.not_found, f"HTTP {code}"
    return FetchStatus.unreachable, f"HTTP {code}"


class Fetcher:
    def __init__(
        self,
        *,
        guard: NetGuard | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        user_agent: str | None = None,
        min_delay: float | None = None,
        timeout: float | None = None,
        concurrency: int | None = None,
    ) -> None:
        settings = get_settings()
        self.guard = guard or NetGuard(
            enabled=settings.netguard_enabled,
            allow_loopback=settings.netguard_allow_loopback,
        )
        self.user_agent = user_agent or f"{USER_AGENT_TOKEN}/0.1 (+{settings.crawler_contact})"
        concurrency = concurrency or settings.crawler_concurrency
        self._throttle = _HostThrottle(
            settings.crawler_min_delay_seconds if min_delay is None else min_delay
        )
        self._semaphore = asyncio.Semaphore(concurrency)
        self._robots: dict[str, _Robots] = {}
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=timeout or settings.crawler_timeout_seconds,
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            headers={"User-Agent": self.user_agent},
            limits=httpx.Limits(max_connections=concurrency),
            event_hooks={"request": [self._before_request]},
        )

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _before_request(self, request: httpx.Request) -> None:
        """Runs for the first request and for every redirect hop."""
        await self.guard.check(str(request.url))
        await self._throttle.wait(request.url.host)

    # ------------------------------------------------------------------ public

    async def fetch(
        self,
        url: str,
        *,
        accept: tuple[str, ...] = (),
        max_bytes: int,
        referer: str | None = None,
        check_robots: bool = True,
        truncate_ok: bool = False,
    ) -> FetchResult:
        """Download one URL. `accept` lists allowed content-type prefixes, e.g. ("text/html",).
        With truncate_ok, a file bigger than max_bytes is not an error: we ask for just the
        first max_bytes (a Range request) and keep only that part."""
        async with self._semaphore:
            if check_robots:
                try:
                    rules = await self._robots_for(url)
                except BlockedByNetworkPolicy as exc:
                    return FetchResult(FetchStatus.blocked, url, detail=f"network policy: {exc}")
                if not rules.allows(url):
                    reason = rules.detail or "robots.txt disallows this page"
                    status = FetchStatus.unreachable if rules.unreachable else FetchStatus.blocked
                    return FetchResult(status, url, detail=reason)
            return await self._get(
                url, accept=accept, max_bytes=max_bytes, referer=referer, truncate_ok=truncate_ok
            )

    async def sitemaps_for(self, url: str) -> list[str]:
        """Sitemap addresses the site announces in its robots.txt."""
        try:
            return list((await self._robots_for(url)).sitemaps)
        except BlockedByNetworkPolicy:
            return []

    # ----------------------------------------------------------------- private

    async def _get(
        self, url: str, *, accept: tuple[str, ...], max_bytes: int, referer: str | None,
        truncate_ok: bool = False,
    ) -> FetchResult:
        headers = {"Referer": referer} if referer else {}
        if truncate_ok:
            headers["Range"] = f"bytes=0-{max_bytes - 1}"
        try:
            async with self._client.stream("GET", url, headers=headers) as response:
                final_url = str(response.url)
                failure = _classify_http_status(response.status_code)
                if failure:
                    status, detail = failure
                    return FetchResult(
                        status, url, final_url, response.status_code, detail=detail
                    )

                content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if accept and not any(content_type.startswith(a) for a in accept):
                    return FetchResult(
                        FetchStatus.unsupported, url, final_url, response.status_code,
                        content_type, detail=f"unexpected content type: {content_type or 'none'}",
                    )

                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > max_bytes and not truncate_ok:
                    return FetchResult(
                        FetchStatus.too_large, url, final_url, response.status_code,
                        content_type, detail="file is larger than the limit",
                    )

                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes and truncate_ok:  # keep the start, drop the rest
                        chunks.append(chunk[: len(chunk) - (size - max_bytes)])
                        return FetchResult(
                            FetchStatus.ok, url, final_url, response.status_code,
                            content_type, b"".join(chunks), detail="truncated",
                        )
                    if size > max_bytes:  # stop reading; the rest is never downloaded
                        return FetchResult(
                            FetchStatus.too_large, url, final_url, response.status_code,
                            content_type, detail="file is larger than the limit",
                        )
                    chunks.append(chunk)
                return FetchResult(
                    FetchStatus.ok, url, final_url, response.status_code,
                    content_type, b"".join(chunks),
                )
        except BlockedByNetworkPolicy as exc:
            return FetchResult(FetchStatus.blocked, url, detail=f"network policy: {exc}")
        except httpx.TooManyRedirects:
            return FetchResult(FetchStatus.unreachable, url, detail="too many redirects")
        except httpx.TimeoutException:
            return FetchResult(FetchStatus.unreachable, url, detail="timed out")
        except (httpx.HTTPError, httpx.InvalidURL, OSError) as exc:
            return FetchResult(FetchStatus.unreachable, url, detail=type(exc).__name__)

    async def _robots_for(self, url: str) -> _Robots:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        loop = asyncio.get_running_loop()
        cached = self._robots.get(origin)
        if cached and loop.time() - cached.fetched_at < ROBOTS_TTL_SECONDS:
            return cached

        result = await self._get(
            f"{origin}/robots.txt", accept=(), max_bytes=ROBOTS_MAX_BYTES, referer=None
        )
        if result.ok:
            parser = RobotFileParser()
            parser.parse(result.body.decode("utf-8", errors="replace").splitlines())
            rules = _Robots(parser=parser, sitemaps=parser.site_maps() or [])
        elif result.http_status is not None and 400 <= result.http_status < 500:
            rules = _Robots(parser=None)  # no robots.txt (or none we may read): no restrictions
        elif result.status == FetchStatus.blocked and result.http_status is None:
            raise BlockedByNetworkPolicy((result.detail or "blocked").removeprefix("network policy: "))
        else:  # server error or unreachable: be careful and do not crawl this time
            rules = _Robots(
                parser=None, deny_all=True, unreachable=True,
                detail=f"robots.txt could not be read ({result.detail})",
            )
        rules.fetched_at = loop.time()
        self._robots[origin] = rules
        return rules