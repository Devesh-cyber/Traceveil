"""Step 10: applies the small edits to files you already have.
Run from the backend folder:   python apply_step10.py
Every edit is checked. If a piece of code is not found exactly once, the script tells you
which file and which snippet, and exits with an error at the end. Safe to run twice."""

import sys
from pathlib import Path

ERRORS = []
CURRENT = {"file": ""}


def sub1(s, old, new, count=1):
    if new in s:
        return s  # already applied
    if s.count(old) != 1:
        ERRORS.append(f"{CURRENT['file']}: expected 1 match, found {s.count(old)} for: {old[:70]!r}")
        return s
    return s.replace(old, new)


_open = open


def open(path, mode="r", *a, **k):  # noqa: A001  remember which file is being edited
    if "r" in mode and "+" not in mode:
        CURRENT["file"] = str(path)
    return _open(path, mode, *a, **k)


if not Path("app/main.py").exists():
    sys.exit("Run this from the backend folder (the one that contains app/).")

# ---------- settings
p='app/core/config.py'; s=open(p).read()
s=sub1(s,"    preserve_match_images: bool = True  # keep a private copy of each matched image as evidence\n","    preserve_match_images: bool = True  # keep a private copy of each matched image as evidence\n\n    # Video\n    max_video_bytes: int = 100_000_000\n    max_video_seconds: int = 600\n    video_verify_enabled: bool = True\n    video_verify_max_bytes: int = 6_000_000\n    video_verify_max_candidates: int = 6\n",1)
open(p,'w').write(s)

# ---------- fetcher: allow reading just the start of a large file
p='app/crawler/fetcher.py'; s=open(p).read()
s=sub1(s,'''        referer: str | None = None,
        check_robots: bool = True,
    ) -> FetchResult:
        """Download one URL. `accept` lists allowed content-type prefixes, e.g. ("text/html",)."""''','''        referer: str | None = None,
        check_robots: bool = True,
        truncate_ok: bool = False,
    ) -> FetchResult:
        """Download one URL. `accept` lists allowed content-type prefixes, e.g. ("text/html",).
        With truncate_ok, a file bigger than max_bytes is not an error: we ask for just the
        first max_bytes (a Range request) and keep only that part."""''')
s=sub1(s,"            return await self._get(url, accept=accept, max_bytes=max_bytes, referer=referer)","            return await self._get(\n                url, accept=accept, max_bytes=max_bytes, referer=referer, truncate_ok=truncate_ok\n            )")
s=sub1(s,'''    async def _get(
        self, url: str, *, accept: tuple[str, ...], max_bytes: int, referer: str | None
    ) -> FetchResult:
        headers = {"Referer": referer} if referer else {}''','''    async def _get(
        self, url: str, *, accept: tuple[str, ...], max_bytes: int, referer: str | None,
        truncate_ok: bool = False,
    ) -> FetchResult:
        headers = {"Referer": referer} if referer else {}
        if truncate_ok:
            headers["Range"] = f"bytes=0-{max_bytes - 1}"''')
s=sub1(s,'''                if declared.isdigit() and int(declared) > max_bytes:''','''                if declared.isdigit() and int(declared) > max_bytes and not truncate_ok:''')
s=sub1(s,'''                    size += len(chunk)
                    if size > max_bytes:  # stop reading; the rest is never downloaded
                        return FetchResult(''','''                    size += len(chunk)
                    if size > max_bytes and truncate_ok:  # keep the start, drop the rest
                        chunks.append(chunk[: len(chunk) - (size - max_bytes)])
                        return FetchResult(
                            FetchStatus.ok, url, final_url, response.status_code,
                            content_type, b"".join(chunks), detail="truncated",
                        )
                    if size > max_bytes:  # stop reading; the rest is never downloaded
                        return FetchResult(''')
open(p,'w').write(s)

# ---------- storage: private temp file + adopting a finished upload
p='app/core/storage.py'; s=open(p).read()
s=sub1(s,'''def read_bytes(relative_path: str) -> bytes:''','''def incoming_path() -> Path:
    """A fresh private file path for an upload that is still being checked."""
    folder = evidence_root() / ".incoming"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder / f"{uuid.uuid4().hex}.part"


def adopt_file(case_id: uuid.UUID, source: Path) -> str:
    """Move a checked upload into its case folder under a random name. Returns the relative path."""
    folder = case_dir(case_id)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    relative = f"{case_id}/{uuid.uuid4().hex}.bin"
    os.replace(source, resolve(relative))
    os.chmod(resolve(relative), 0o600)
    return relative


def read_bytes(relative_path: str) -> bytes:''')
open(p,'w').write(s)

# ---------- matching: one common 'matcher' shape for images and video
p='app/matching/verify.py'; s=open(p).read()
if 'class ImageMatcher' not in s:
  s+='''

class ImageMatcher:
    """Compares candidates with one reference image."""

    def __init__(self, reference: Reference) -> None:
        self.reference = reference

    def compare(self, image: Image.Image) -> MatchDecision | None:
        return evaluate_candidate(self.reference, image)


def as_matcher(reference_or_matcher):
    """Anything with a .compare(image) method is already a matcher (the video matcher is one);
    a plain Reference is wrapped."""
    return reference_or_matcher if hasattr(reference_or_matcher, "compare") else ImageMatcher(reference_or_matcher)
'''
open(p,'w').write(s)

# ---------- runner: use a matcher, and allow an 'enrich' step before a match is saved
p='app/crawler/runner.py'; s=open(p).read()
s=sub1(s,"from app.matching.verify import evaluate_candidate","from app.matching.verify import as_matcher")
s=sub1(s,'''def _compare_image(reference: Reference, data: bytes) -> MatchDecision | None:
    """Decode and compare one downloaded picture. Runs in a worker thread."""
    try:
        image = load_image(data)
    except ImageError:
        return None
    return evaluate_candidate(reference, image)''','''def _compare_image(matcher, data: bytes) -> MatchDecision | None:
    """Decode and compare one downloaded picture. Runs in a worker thread."""
    try:
        image = load_image(data)
    except ImageError:
        return None
    return matcher.compare(image)''')
s=sub1(s,'''MatchCallback = Callable[[MatchFound], Awaitable[None]]''','''MatchCallback = Callable[[MatchFound], Awaitable[None]]
EnrichCallback = Callable[[MatchFound, str], Awaitable[None]]  # (match, the page's HTML)''')
s=sub1(s,'''    cpu_slots: asyncio.Semaphore,
    on_match: MatchCallback | None = None,
) -> SiteOutcome:''','''    cpu_slots: asyncio.Semaphore,
    on_match: MatchCallback | None = None,
    enrich: EnrichCallback | None = None,
) -> SiteOutcome:''')
s=sub1(s,'''                outcome.matches.append(found)
                progress.matches_found += 1
                if on_match:''','''                if enrich:  # e.g. look for the video on the page and check it
                    try:
                        await enrich(found, html)
                    except Exception:
                        log.exception("enriching a match on %s failed", entry.domain)
                outcome.matches.append(found)
                progress.matches_found += 1
                if on_match:''')
s=sub1(s,'''    on_site_done: SiteCallback | None = None,
) -> ScanSummary:''','''    on_site_done: SiteCallback | None = None,
    enrich: EnrichCallback | None = None,
) -> ScanSummary:''')
s=sub1(s,'''    limits = limits or ScanLimits.from_settings()
    progress = progress or ScanProgress()''','''    limits = limits or ScanLimits.from_settings()
    reference = as_matcher(reference)
    progress = progress or ScanProgress()''')
s=sub1(s,"entry, reference, fetcher, budget, limits, progress, cpu_slots, on_match\n                    )","entry, reference, fetcher, budget, limits, progress, cpu_slots, on_match, enrich\n                    )")
open(p,'w').write(s)

# ---------- scans: a video can be the reference too
p='app/api/routes/scans.py'; s=open(p).read()
s=sub1(s,"            EvidenceFile.kind == EvidenceKind.reference_image,\n",
    "            EvidenceFile.kind.in_([EvidenceKind.reference_image, EvidenceKind.reference_video]),\n")
open(p,'w').write(s)

# ---------- report: list both kinds and say which
p='app/reporting/evidence_report.py'; s=open(p).read()
s=sub1(s,"EvidenceFile.kind == EvidenceKind.reference_image)",
    "EvidenceFile.kind.in_([EvidenceKind.reference_image, EvidenceKind.reference_video]))")
s=sub1(s,'{"id": str(r.id), "filename": r.original_filename, "sha256": r.sha256,',
    '{"id": str(r.id), "type": "video" if r.kind == EvidenceKind.reference_video else "image",\n             "filename": r.original_filename, "sha256": r.sha256,')
s=sub1(s,"lines.append(f\"- {r['filename']}: SHA-256","lines.append(f\"- {r['filename']} ({r['type']}): SHA-256")
open(p,'w').write(s)

# ---------- evidence route: video upload
p='app/api/routes/evidence.py'; s=open(p).read()
s=sub1(s,"import io\nfrom datetime","import hashlib\nimport io\nimport os\nfrom datetime")
s=sub1(s,"from app.schemas.evidence import EvidenceOut\n",
    "from app.schemas.evidence import EvidenceOut\nfrom app.video.keyframes import VideoError, extract_keyframes, probe\n")
ROUTE = '@router.post("/video", response_model=EvidenceOut, status_code=status.HTTP_201_CREATED)\ndef upload_reference_video(\n    file: UploadFile,\n    case: Case = Depends(get_owned_case),\n    user: User = Depends(get_current_user),\n    db: Session = Depends(get_db),\n) -> EvidenceFile:\n    """Accept a video the user is authorised to use as the reference for a scan.\n\n    It is streamed to a private temporary file (never held in memory), cut off at the size\n    limit, and only moved into the case folder after we have proved that it is a short video\n    with usable frames. Whatever goes wrong, the temporary file is removed."""\n    settings = get_settings()\n    temp = storage.incoming_path()\n    digest, size = hashlib.sha256(), 0\n    try:\n        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)\n        with os.fdopen(fd, "wb") as out:\n            while chunk := file.file.read(1024 * 1024):\n                size += len(chunk)\n                if size > settings.max_video_bytes:\n                    raise HTTPException(413, "File is too large.")\n                digest.update(chunk)\n                out.write(chunk)\n        if size == 0:\n            raise HTTPException(422, "The file is empty.")\n        try:\n            info = probe(temp, settings.max_video_seconds)\n            extract_keyframes(temp, info)  # proves there are real, usable frames\n        except VideoError as exc:\n            raise HTTPException(422, str(exc)) from exc\n        relative_path = storage.adopt_file(case.id, temp)\n    except BaseException:\n        temp.unlink(missing_ok=True)\n        raise\n\n    declared = (file.content_type or "").lower()\n    try:\n        evidence = EvidenceFile(\n            case_id=case.id,\n            kind=EvidenceKind.reference_video,\n            original_filename=_safe_name(file.filename),\n            content_type=declared[:100] if declared.startswith("video/") else "video/unknown",\n            size_bytes=size,\n            sha256=digest.hexdigest(),\n            storage_path=relative_path,\n            delete_after=datetime.now(timezone.utc) + timedelta(days=settings.retention_days),\n        )\n        db.add(evidence)\n        db.flush()\n        record(db, "evidence.uploaded", user_id=user.id, case_id=case.id,\n               evidence_id=str(evidence.id), sha256=evidence.sha256, **info.as_dict())\n        db.commit()\n    except Exception:\n        db.rollback()\n        storage.delete_file(relative_path)\n        raise\n    return evidence\n\n\n'
s=sub1(s,'@router.get("", response_model=list[EvidenceOut])', ROUTE + '@router.get("", response_model=list[EvidenceOut])')
open(p,'w').write(s)

# ---------- worker: build a video matcher when the reference is a video
p='app/worker/main.py'; s=open(p).read()
s=sub1(s,"from app.matching.fingerprint import Reference, build_reference, load_image\n",
    "from app.matching.fingerprint import Reference, build_reference, load_image\n"
    "from app.video.keyframes import VideoError, extract_keyframes, probe\n"
    "from app.video.matcher import KeyframeRef, VideoMatcher\n"
    "from app.video.verify import VideoEnricher\n")
s=sub1(s,'''def _load_reference(scan_id: uuid.UUID) -> Reference:
    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        evidence = db.get(EvidenceFile, scan.evidence_file_id)
        data = storage.read_bytes(evidence.storage_path)
    return build_reference(load_image(data))
''', '''def _build_video_matcher(path) -> tuple[VideoMatcher, dict]:
    """Pick the frames that represent the user's video and fingerprint each of them."""
    settings = get_settings()
    info = probe(path, settings.max_video_seconds)
    keyframes = extract_keyframes(path, info)
    refs = [KeyframeRef(k.time_seconds, build_reference(k.image)) for k in keyframes]
    return VideoMatcher(refs), {"type": "video", "keyframes": len(refs), **info.as_dict()}


def _load_reference(scan_id: uuid.UUID):
    """Returns (what to compare against, a few facts about it for the scan's record).
    For an image that is a Reference; for a video it is a VideoMatcher."""
    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        evidence = db.get(EvidenceFile, scan.evidence_file_id)
        kind, stored = evidence.kind, evidence.storage_path
    if kind == EvidenceKind.reference_video:
        return _build_video_matcher(storage.resolve(stored))
    return build_reference(load_image(storage.read_bytes(stored))), {"type": "image"}
''')
s=sub1(s,"def _write_progress(", '''def _merge_stats(scan_id: uuid.UUID, extra: dict) -> None:
    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        if scan is not None and scan.status == ScanStatus.running:
            scan.stats = {**(scan.stats or {}), **extra}
            db.commit()


def _write_progress(''')
s=sub1(s,"        reference = await asyncio.to_thread(_load_reference, scan_id)\n", '''        try:
            reference, reference_facts = await asyncio.to_thread(_load_reference, scan_id)
        except VideoError as exc:
            _finish(scan_id, ScanStatus.failed, error=f"The reference video could not be used: {exc}")
            return
        _merge_stats(scan_id, {"reference": reference_facts})
        enrich = VideoEnricher(fetcher, reference) if isinstance(reference, VideoMatcher) else None
''')
s=sub1(s,"progress=progress, on_match=on_match, on_site_done=on_site_done,\n        ))",
    "progress=progress, on_match=on_match, on_site_done=on_site_done, enrich=enrich,\n        ))")
open(p,'w').write(s)

if ERRORS:
    print("SOME EDITS DID NOT APPLY:")
    for e in ERRORS:
        print(" -", e)
    sys.exit(1)
print("Step 10 edits applied.")
