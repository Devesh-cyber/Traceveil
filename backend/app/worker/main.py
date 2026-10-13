"""The background worker. Run it as its own process:

    python -m app.worker.main

It picks up scans that the API has queued, runs the crawler, and saves what it finds in
the database. Because the work is stored there (not in the worker's memory), the API can
show real progress, and a restart never loses track of a scan.
"""

from __future__ import annotations

import asyncio
import io
import logging
import signal
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from PIL import Image
from sqlalchemy import select

from app.core import storage
from app.core.audit import record
from app.core.config import get_settings
from app.crawler.domains import DomainEntry, load_domains
from app.crawler.fetcher import Fetcher
from app.crawler.runner import MatchFound, ScanLimits, ScanProgress, ScanSummary, SiteOutcome, run_scan
from app.db.session import SessionLocal
from app.matching.fingerprint import Reference, build_reference, load_image
from app.models import DomainResult, EvidenceFile, EvidenceKind, Match, Scan, ScanStatus

log = logging.getLogger("worker")
PROGRESS_SECONDS = 2.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ database helpers
# Each helper opens its own short session, so the worker never holds a transaction open
# while it waits for websites.

def recover_stuck_scans() -> int:
    """At start-up, any scan still marked 'running' belongs to a worker that died."""
    with SessionLocal() as db:
        stuck = list(db.scalars(select(Scan).where(Scan.status == ScanStatus.running)))
        for scan in stuck:
            scan.status = ScanStatus.failed
            scan.error = "The worker stopped while this scan was running."
            scan.finished_at = _now()
            record(db, "scan.failed", case_id=scan.case_id, scan_id=str(scan.id), reason="worker restarted")
        db.commit()
        return len(stuck)


def claim_next_scan(scan_id: uuid.UUID | None = None) -> uuid.UUID | None:
    """Take the oldest queued scan (or a specific one) and mark it running.
    SKIP LOCKED means two workers can never take the same scan."""
    with SessionLocal() as db:
        query = select(Scan).where(Scan.status == ScanStatus.queued)
        if scan_id is not None:
            query = query.where(Scan.id == scan_id)
        scan = db.scalar(query.order_by(Scan.created_at).limit(1).with_for_update(skip_locked=True))
        if scan is None:
            return None
        scan.status = ScanStatus.running
        scan.started_at = _now()
        record(db, "scan.started", case_id=scan.case_id, scan_id=str(scan.id))
        db.commit()
        return scan.id


def _status_of(scan_id: uuid.UUID) -> ScanStatus | None:
    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        return scan.status if scan else None


def _write_progress(scan_id: uuid.UUID, progress: ScanProgress) -> None:
    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        if scan is not None and scan.status == ScanStatus.running:
            scan.stats = {**(scan.stats or {}), **asdict(progress)}
            db.commit()


def _save_site(scan_id: uuid.UUID, outcome: SiteOutcome) -> None:
    with SessionLocal() as db:
        db.add(DomainResult(
            scan_id=scan_id, domain=outcome.domain, status=outcome.status,
            pages_scanned=outcome.pages_scanned, images_compared=outcome.images_compared,
            detail=outcome.detail, checked_at=_now(),
        ))
        db.commit()


CONTENT_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def _preserve_copy(db, case_id: uuid.UUID, found: MatchFound) -> uuid.UUID | None:
    """Keep a private copy of the matched picture as evidence. The page may be taken down
    or changed later, and a takedown request is much stronger with proof of what was there."""
    if not found.image_bytes:
        return None
    try:
        content_type = CONTENT_TYPES.get(Image.open(io.BytesIO(found.image_bytes)).format or "")
    except Exception:
        return None
    if content_type is None:
        return None
    relative_path, digest = storage.save_bytes(case_id, found.image_bytes)
    name = found.image_url.split("?")[0].rsplit("/", 1)[-1][:200] or "matched-image"
    evidence = EvidenceFile(
        case_id=case_id, kind=EvidenceKind.supporting, original_filename=name,
        content_type=content_type, size_bytes=len(found.image_bytes), sha256=digest,
        storage_path=relative_path,
        delete_after=_now() + timedelta(days=get_settings().retention_days),
    )
    db.add(evidence)
    db.flush()
    return evidence.id


def _save_match(scan_id: uuid.UUID, found: MatchFound) -> None:
    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        snapshot_id = None
        if get_settings().preserve_match_images:
            snapshot_id = _preserve_copy(db, scan.case_id, found)
        db.add(Match(
            scan_id=scan_id, domain=found.domain,
            source_page_url=found.source_page_url, image_url=found.image_url,
            kind=found.decision.kind,
            signals={**found.decision.signals, "variant": found.decision.variant,
                     "collected_at": found.collected_at.isoformat()},
            snapshot_id=snapshot_id,
        ))
        db.commit()


def _finish(
    scan_id: uuid.UUID,
    status: ScanStatus,
    *,
    error: str | None = None,
    stats: dict | None = None,
    truncated: bool = False,
    truncation_reason: str | None = None,
) -> None:
    """Record the final result. A scan that was cancelled meanwhile stays cancelled."""
    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        if scan is None or scan.status != ScanStatus.running:
            return
        scan.status = status
        scan.error = error
        scan.finished_at = _now()
        scan.truncated = truncated
        scan.truncation_reason = truncation_reason
        if stats:
            scan.stats = {**(scan.stats or {}), **stats}
        record(db, f"scan.{status.value}", case_id=scan.case_id, scan_id=str(scan.id),
               error=error, stats=scan.stats)
        db.commit()


# ------------------------------------------------------------------ the job itself

def _load_reference(scan_id: uuid.UUID) -> Reference:
    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        evidence = db.get(EvidenceFile, scan.evidence_file_id)
        data = storage.read_bytes(evidence.storage_path)
    return build_reference(load_image(data))


def _domains_for_scan() -> list[DomainEntry]:
    entries, problems = load_domains()
    for problem in problems:
        log.warning("domains.csv: %s", problem)
    for url in get_settings().scan_dev_targets:  # development only
        entries.append(DomainEntry(domain=urlsplit(url).netloc, base_url=url))
    return entries


def _finish_from_summary(scan_id: uuid.UUID, summary: ScanSummary, progress: ScanProgress) -> None:
    stats = {**asdict(progress), "coverage": summary.coverage()}
    if progress.images_compared == 0:
        # Nothing was compared at all. Calling this "completed, no matches" would be a lie.
        _finish(
            scan_id, ScanStatus.failed, stats=stats,
            error="No pictures could be compared. See the per-site results for the reasons.",
            truncated=summary.truncated, truncation_reason=summary.truncation_reason,
        )
    else:
        _finish(
            scan_id, ScanStatus.completed, stats=stats,
            truncated=summary.truncated, truncation_reason=summary.truncation_reason,
        )


async def process_scan(
    scan_id: uuid.UUID,
    *,
    fetcher: Fetcher | None = None,
    domains: list[DomainEntry] | None = None,
    limits: ScanLimits | None = None,
    poll_seconds: float = PROGRESS_SECONDS,
) -> None:
    """Run one scan that is already marked 'running'."""
    own_fetcher = fetcher is None
    fetcher = fetcher or Fetcher()
    progress = ScanProgress()
    try:
        reference = await asyncio.to_thread(_load_reference, scan_id)
        entries = domains if domains is not None else _domains_for_scan()
        if not entries:
            _finish(scan_id, ScanStatus.failed,
                    error="No websites are configured. Fill in backend/data/domains.csv.")
            return

        async def on_match(found: MatchFound) -> None:
            _save_match(scan_id, found)

        async def on_site_done(outcome: SiteOutcome) -> None:
            _save_site(scan_id, outcome)

        task = asyncio.create_task(run_scan(
            entries, reference, fetcher, limits or ScanLimits.from_settings(),
            progress=progress, on_match=on_match, on_site_done=on_site_done,
        ))
        cancelled = False
        while not task.done():
            await asyncio.wait({task}, timeout=poll_seconds)
            if task.done():
                break
            _write_progress(scan_id, progress)
            if _status_of(scan_id) == ScanStatus.cancelled:  # the user pressed cancel
                cancelled = True
                task.cancel()
                break

        if cancelled:
            try:
                await task
            except asyncio.CancelledError:
                pass
            log.info("scan %s cancelled", scan_id)
            return
        _finish_from_summary(scan_id, task.result(), progress)

    except asyncio.CancelledError:  # the worker itself is shutting down
        _finish(scan_id, ScanStatus.failed, error="The worker was shut down during this scan.")
        raise
    except Exception as exc:
        log.exception("scan %s failed", scan_id)
        _finish(scan_id, ScanStatus.failed, error=f"internal error: {type(exc).__name__}")
    finally:
        if own_fetcher:
            await fetcher.aclose()


# ------------------------------------------------------------------ the loop

async def worker_loop(stop: asyncio.Event) -> None:
    recovered = recover_stuck_scans()
    if recovered:
        log.warning("marked %d interrupted scan(s) as failed", recovered)
    poll = get_settings().worker_poll_seconds
    log.info("worker ready")
    while not stop.is_set():
        scan_id = claim_next_scan()
        if scan_id is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll)
            except asyncio.TimeoutError:
                pass
            continue
        log.info("running scan %s", scan_id)
        await process_scan(scan_id)


async def _main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    await worker_loop(stop)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # it would log every address we visit
    asyncio.run(_main())