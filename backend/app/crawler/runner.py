"""Run a scan: visit each website, compare its pictures with the reference, and report
what happened to EVERY site.

This module knows nothing about the database or the API. It returns plain results
(and calls optional callbacks), and Step 8 stores them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.crawler.assets import extract_image_urls
from app.crawler.domains import DomainEntry
from app.crawler.fetcher import Fetcher, FetchResult, FetchStatus
from app.crawler.pages import discover_pages
from app.matching.fingerprint import ImageError, Reference, load_image
from app.matching.tiers import MatchDecision
from app.matching.verify import evaluate_candidate
from app.models.domain_result import DomainStatus

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanLimits:
    max_pages_per_domain: int = 15
    max_images_per_page: int = 32
    max_total_images: int = 8000
    max_seconds: float = 600
    parallel_domains: int = 6
    cpu_workers: int = 4
    max_html_bytes: int = 3_000_000
    max_image_bytes: int = 3_000_000

    @classmethod
    def from_settings(cls) -> "ScanLimits":
        s = get_settings()
        return cls(
            max_pages_per_domain=s.scan_max_pages_per_domain,
            max_images_per_page=s.scan_max_images_per_page,
            max_total_images=s.scan_max_total_images,
            max_seconds=s.scan_max_seconds,
            parallel_domains=s.scan_parallel_domains,
            cpu_workers=s.scan_cpu_workers,
            max_html_bytes=s.max_html_bytes,
            max_image_bytes=s.max_image_bytes,
        )


@dataclass
class ScanProgress:
    """Live counters. Step 8 reads these while the scan runs."""

    sites_total: int = 0
    sites_done: int = 0
    pages_scanned: int = 0
    images_compared: int = 0
    images_failed: int = 0
    matches_found: int = 0


class ScanBudget:
    """Stops a scan that runs too long or looks at too many images, and remembers why."""

    def __init__(self, limits: ScanLimits) -> None:
        self._limits = limits
        self._deadline = time.monotonic() + limits.max_seconds
        self._images_left = limits.max_total_images
        self.reason: str | None = None

    @property
    def exhausted(self) -> bool:
        if self.reason is None and time.monotonic() >= self._deadline:
            self.reason = "time limit reached"
        return self.reason is not None

    def take_image(self) -> bool:
        if self.exhausted:
            return False
        if self._images_left <= 0:
            self.reason = "image limit reached"
            return False
        self._images_left -= 1
        return True


@dataclass
class MatchFound:
    domain: str
    source_page_url: str
    image_url: str
    decision: MatchDecision


@dataclass
class SiteOutcome:
    domain: str
    status: DomainStatus
    pages_scanned: int = 0
    images_compared: int = 0
    detail: str | None = None
    matches: list[MatchFound] = field(default_factory=list)


@dataclass
class ScanSummary:
    outcomes: list[SiteOutcome]
    truncated: bool = False
    truncation_reason: str | None = None

    @property
    def matches(self) -> list[MatchFound]:
        return [m for outcome in self.outcomes for m in outcome.matches]

    def coverage(self) -> dict[str, int]:
        counts = {status.value: 0 for status in DomainStatus}
        for outcome in self.outcomes:
            counts[outcome.status.value] += 1
        return counts


MatchCallback = Callable[[MatchFound], Awaitable[None]]
SiteCallback = Callable[[SiteOutcome], Awaitable[None]]


def _compare_image(reference: Reference, data: bytes) -> MatchDecision | None:
    """Decode and compare one downloaded picture. Runs in a worker thread."""
    try:
        image = load_image(data)
    except ImageError:
        return None
    return evaluate_candidate(reference, image)


def _homepage_failure(result: FetchResult) -> SiteOutcome:
    """Explain why we could not even read a website's first page."""
    status = DomainStatus.blocked if result.status == FetchStatus.blocked else DomainStatus.unreachable
    return SiteOutcome(domain="", status=status, detail=result.detail or result.status.value)


async def scan_domain(
    entry: DomainEntry,
    reference: Reference,
    fetcher: Fetcher,
    budget: ScanBudget,
    limits: ScanLimits,
    progress: ScanProgress,
    cpu_slots: asyncio.Semaphore,
    on_match: MatchCallback | None = None,
) -> SiteOutcome:
    discovery = await discover_pages(
        fetcher, entry.start_url, limits.max_pages_per_domain, limits.max_html_bytes
    )
    if not discovery.homepage.ok:
        outcome = _homepage_failure(discovery.homepage)
        outcome.domain = entry.domain
        return outcome

    outcome = SiteOutcome(domain=entry.domain, status=DomainStatus.checked)
    seen_images: set[str] = set()
    images_seen = 0
    failure_kinds: set[FetchStatus] = set()
    stopped_early = False

    for page_url in discovery.pages:
        if budget.exhausted:
            stopped_early = True
            break
        if page_url == discovery.pages[0]:
            html_result = discovery.homepage
        else:
            html_result = await fetcher.fetch(
                page_url, accept=("text/html", "application/xhtml"),
                max_bytes=limits.max_html_bytes, referer=discovery.pages[0],
            )
        if not html_result.ok:
            continue
        outcome.pages_scanned += 1
        progress.pages_scanned += 1

        html = html_result.body.decode("utf-8", errors="replace")
        for image_url in extract_image_urls(html, page_url, limits.max_images_per_page):
            if image_url in seen_images:  # the same picture on many pages is fetched once
                continue
            seen_images.add(image_url)
            images_seen += 1
            if not budget.take_image():
                stopped_early = True
                break

            picture = await fetcher.fetch(
                image_url, accept=("image/",), max_bytes=limits.max_image_bytes, referer=page_url
            )
            if not picture.ok:
                progress.images_failed += 1
                failure_kinds.add(picture.status)
                continue

            async with cpu_slots:
                decision = await asyncio.to_thread(_compare_image, reference, picture.body)
            outcome.images_compared += 1
            progress.images_compared += 1
            if decision:
                found = MatchFound(entry.domain, page_url, image_url, decision)
                outcome.matches.append(found)
                progress.matches_found += 1
                if on_match:
                    await on_match(found)
        if stopped_early:
            break

    if images_seen == 0:
        outcome.status = DomainStatus.no_images
        outcome.detail = "pages were readable but contained no usable pictures"
    elif outcome.images_compared == 0:
        # pictures existed but none could be downloaded: that is NOT "no match"
        blocked = FetchStatus.blocked in failure_kinds
        outcome.status = DomainStatus.blocked if blocked else DomainStatus.unreachable
        outcome.detail = "pictures were found but none could be downloaded"
    if stopped_early:
        note = f"stopped early: {budget.reason}"
        outcome.detail = f"{outcome.detail}; {note}" if outcome.detail else note
    return outcome


async def run_scan(
    domains: list[DomainEntry],
    reference: Reference,
    fetcher: Fetcher,
    limits: ScanLimits | None = None,
    progress: ScanProgress | None = None,
    on_match: MatchCallback | None = None,
    on_site_done: SiteCallback | None = None,
) -> ScanSummary:
    limits = limits or ScanLimits.from_settings()
    progress = progress or ScanProgress()
    progress.sites_total = len(domains)
    budget = ScanBudget(limits)
    site_slots = asyncio.Semaphore(limits.parallel_domains)
    cpu_slots = asyncio.Semaphore(limits.cpu_workers)

    async def one(entry: DomainEntry) -> SiteOutcome:
        async with site_slots:
            if budget.exhausted:
                outcome = SiteOutcome(entry.domain, DomainStatus.skipped, detail=budget.reason)
            else:
                try:
                    outcome = await scan_domain(
                        entry, reference, fetcher, budget, limits, progress, cpu_slots, on_match
                    )
                except Exception as exc:  # one broken site must never stop the scan
                    log.exception("scan of %s failed", entry.domain)
                    outcome = SiteOutcome(
                        entry.domain, DomainStatus.unreachable,
                        detail=f"internal error: {type(exc).__name__}",
                    )
            progress.sites_done += 1
            if on_site_done:
                await on_site_done(outcome)
            return outcome

    outcomes = await asyncio.gather(*(one(entry) for entry in domains))
    return ScanSummary(
        outcomes=list(outcomes),
        truncated=budget.reason is not None,
        truncation_reason=budget.reason,
    )