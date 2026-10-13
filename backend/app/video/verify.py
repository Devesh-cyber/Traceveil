"""Stage 3: check the page's actual video file against the user's video.

A thumbnail can match while the video behind it is something else. So for pages that offer a
plain video file we download only the first few megabytes, take a frame every couple of
seconds, and look for our keyframes IN ORDER. A copy of the user's video shows the same
scenes in the same order. A different video with a similar-looking thumbnail does not.

The machine result is called 'sequence_confirmed'. It is still only a machine finding. The
word 'verified' is reserved for a person confirming it (see the review levels).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.crawler.fetcher import Fetcher
from app.matching.tiers import MatchDecision
from app.models.match import MatchKind
from app.video.discover import find_video_urls
from app.video.keyframes import sample_frames
from app.video.matcher import VideoMatcher

STEP_SECONDS = 2.0
MAX_FRAMES = 12
REQUIRED_ORDERED_FRAMES = 3
FRAME_KINDS = {MatchKind.exact, MatchKind.near_duplicate, MatchKind.structural}  # 'probable' is too weak here


@dataclass
class VideoVerification:
    status: str  # sequence_confirmed | partial | no_frame_match | not_checked
    frames_checked: int = 0
    frames_matched: int = 0
    ordered_frames: int = 0
    seconds_checked: float = 0.0
    segment: dict | None = None
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "verification": self.status, "frames_checked": self.frames_checked,
            "frames_matched": self.frames_matched, "ordered_frames": self.ordered_frames,
            "seconds_checked": round(self.seconds_checked, 1),
            "matched_segment": self.segment, "reason": self.reason,
        }


def longest_ordered_run(pairs: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """pairs = (time in their video, time in ours). Returns the longest run where our time
    keeps increasing as their time increases: the same scenes, in the same order."""
    pairs = sorted(pairs)
    best: list[list[tuple[float, float]]] = []
    for i, pair in enumerate(pairs):
        run = [pair]
        for j in range(i):
            if pairs[j][1] < pair[1] and len(best[j]) + 1 > len(run):
                run = best[j] + [pair]
        best.append(run)
    return max(best, key=len, default=[])


def compare_frames(matcher: VideoMatcher, path: Path) -> VideoVerification:
    """Decode a (possibly partial) video file and look for the user's keyframes. CPU-bound."""
    frames = sample_frames(path, step_seconds=STEP_SECONDS, max_frames=MAX_FRAMES)
    if not frames:
        return VideoVerification(
            "not_checked", reason="the downloaded part could not be decoded "
            "(some files need to be read from the end, so a partial download cannot be played)",
        )
    seconds = frames[-1][0] + STEP_SECONDS
    pairs = []
    for their_time, image in frames:
        best = matcher.best_keyframe(image)
        if best and best[1].kind in FRAME_KINDS:
            pairs.append((their_time, best[0]))
    if not pairs:
        return VideoVerification("no_frame_match", len(frames), 0, 0, seconds,
                                 reason="no frame in the part we checked matched; this does not rule out a match later in the video")
    run = longest_ordered_run(pairs)
    if len(run) >= REQUIRED_ORDERED_FRAMES:
        return VideoVerification(
            "sequence_confirmed", len(frames), len(pairs), len(run), seconds,
            segment={"start": run[0][0], "end": run[-1][0]},
        )
    return VideoVerification(
        "partial", len(frames), len(pairs), len(run), seconds,
        reason=f"some frames matched but fewer than {REQUIRED_ORDERED_FRAMES} in the right order",
    )


async def verify_video_url(fetcher: Fetcher, matcher: VideoMatcher, url: str, max_bytes: int) -> VideoVerification:
    result = await fetcher.fetch(url, accept=("video/", "application/octet-stream"),
                                 max_bytes=max_bytes, truncate_ok=True)
    if not result.ok:
        return VideoVerification("not_checked", reason=f"the video could not be downloaded ({result.detail or result.status.value})")
    fd, name = tempfile.mkstemp(suffix=".video")  # private (mode 0600) and always removed
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(result.body)
        return await asyncio.to_thread(compare_frames, matcher, Path(name))
    finally:
        Path(name).unlink(missing_ok=True)


class VideoEnricher:
    """Plugged into the crawler: for each thumbnail match, look for the page's video and,
    for a limited number of plain files, check it. Results are stored in the match's signals."""

    def __init__(self, fetcher: Fetcher, matcher: VideoMatcher) -> None:
        s = get_settings()
        self.fetcher, self.matcher = fetcher, matcher
        self.enabled = s.video_verify_enabled
        self.max_bytes = s.video_verify_max_bytes
        self.remaining = s.video_verify_max_candidates
        self._seen_pages: set[str] = set()

    async def __call__(self, found, html: str) -> None:
        refs = find_video_urls(html, found.source_page_url)
        video: dict = {"urls": [ref.as_dict() for ref in refs]}
        direct = [ref for ref in refs if ref.kind == "direct"]
        if not refs:
            video["verification"] = "not_checked"
            video["reason"] = "no video was found on the page (it may be built by scripts)"
        elif not direct:
            video["verification"] = "not_checked"
            video["reason"] = "the page's video is a stream or an embedded player, which we do not download"
        elif not self.enabled or self.remaining <= 0 or found.source_page_url in self._seen_pages:
            video["verification"] = "not_checked"
            video["reason"] = "video checking was switched off or its limit for this scan was reached"
        else:
            self.remaining -= 1
            self._seen_pages.add(found.source_page_url)
            video["checked_url"] = direct[0].url
            video.update((await verify_video_url(self.fetcher, self.matcher, direct[0].url, self.max_bytes)).as_dict())
        found.decision.signals = {**found.decision.signals, "video": video}
