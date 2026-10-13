"""Reading a video safely, and choosing the frames that represent it.

Decoding a video file that came from outside is the riskiest thing this program does,
because video decoders are large and have had security bugs. So we check every limit
(size, length, dimensions, time spent) before and while decoding, and keep the OpenCV
package up to date. For a bigger deployment, run this module in its own locked-down container.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import imagehash
import numpy as np
from PIL import Image

MAX_KEYFRAMES = 16
CANDIDATE_SAMPLES = 40     # frames looked at, spread evenly over the whole video
MIN_DIVERSITY_BITS = 5     # two kept frames must differ by at least this many fingerprint bits
FRAME_MAX_SIDE = 640
MIN_DIMENSION, MAX_DIMENSION = 32, 7680
BLACK_MEAN = 14            # almost black frames (fade-ins) look alike and cause false matches
FLAT_STD = 8               # so do frames of one flat colour
EXTRACT_TIMEOUT_SECONDS = 30
MAX_FPS = 240


class VideoError(ValueError):
    """The file is not a video we are willing to decode."""


@dataclass(frozen=True)
class VideoInfo:
    duration_seconds: float
    fps: float
    frames: int
    width: int
    height: int

    def as_dict(self) -> dict:
        return {"duration_seconds": round(self.duration_seconds, 2), "fps": round(self.fps, 2),
                "frames": self.frames, "width": self.width, "height": self.height}


@dataclass
class Keyframe:
    time_seconds: float
    image: Image.Image


def probe(path: Path, max_seconds: float) -> VideoInfo:
    """Check that the file is a short, ordinary video, by decoding its first frame."""
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise VideoError("File is not a readable video.")
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        ok, _ = cap.read()
        if not ok:
            raise VideoError("File is not a readable video.")
        if not (0 < fps <= MAX_FPS):
            raise VideoError("The video's frame rate is not usable.")
        if frames <= 0:
            frames = _count_by_grabbing(cap, int(max_seconds * fps) + 1)
        if frames <= 0:
            raise VideoError("The video has no frames.")
        if not (MIN_DIMENSION <= width <= MAX_DIMENSION and MIN_DIMENSION <= height <= MAX_DIMENSION):
            raise VideoError("The video's picture size is not supported.")
        duration = frames / fps
        if duration > max_seconds:
            raise VideoError(f"The video is longer than {int(max_seconds)} seconds.")
        return VideoInfo(duration, fps, frames, width, height)
    finally:
        cap.release()


def _count_by_grabbing(cap: cv2.VideoCapture, limit: int) -> int:
    """Some containers do not say how many frames they hold, so count them (up to `limit`)."""
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    count = 0
    while count <= limit and cap.grab():
        count += 1
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    return count


def _to_image(frame: np.ndarray) -> Image.Image:
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    longest = max(image.size)
    if longest > FRAME_MAX_SIDE:
        scale = FRAME_MAX_SIDE / longest
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))))
    return image


def _usable(image: Image.Image) -> bool:
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    return float(gray.mean()) >= BLACK_MEAN and float(gray.std()) >= FLAT_STD


def _select_diverse(candidates: list[tuple[float, Image.Image, imagehash.ImageHash]], limit: int):
    """Keep the frames that look most different from each other (greedy farthest-point)."""
    if not candidates:
        return []
    chosen = [candidates[0]]
    remaining = candidates[1:]
    while remaining and len(chosen) < limit:
        best, best_distance = None, -1
        for item in remaining:
            distance = min(int(item[2] - c[2]) for c in chosen)
            if distance > best_distance:
                best, best_distance = item, distance
        if best is None or best_distance < MIN_DIVERSITY_BITS:
            break
        chosen.append(best)
        remaining.remove(best)
    return sorted(chosen, key=lambda item: item[0])


def extract_keyframes(
    path: Path, info: VideoInfo, *, max_keyframes: int = MAX_KEYFRAMES,
    samples: int = CANDIDATE_SAMPLES, timeout: float = EXTRACT_TIMEOUT_SECONDS,
) -> list[Keyframe]:
    """Sample frames evenly over the video, drop black and flat ones, then keep up to
    `max_keyframes` that differ most from each other."""
    deadline = time.monotonic() + timeout
    indexes = sorted({int(i) for i in np.linspace(0, max(0, info.frames - 1), samples)})
    candidates = []
    cap = cv2.VideoCapture(str(path))
    try:
        for index in indexes:
            if time.monotonic() > deadline:
                break
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = cap.read()
            if not ok:
                continue
            image = _to_image(frame)
            if _usable(image):
                candidates.append((index / info.fps, image, imagehash.phash(image)))
    finally:
        cap.release()
    chosen = _select_diverse(candidates, max_keyframes)
    if not chosen:
        raise VideoError("The video has no usable frames (it may be all black or one flat colour).")
    return [Keyframe(t, image) for t, image, _ in chosen]


def sample_frames(path: Path, *, step_seconds: float, max_frames: int, timeout: float = 30):
    """Decode from the start and keep one frame every `step_seconds`. Works on a file that was
    cut short (a partial download): it simply stops where the data stops."""
    deadline = time.monotonic() + timeout
    cap = cv2.VideoCapture(str(path))
    frames: list[tuple[float, Image.Image]] = []
    try:
        if not cap.isOpened():
            return frames
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        if not (0 < fps <= MAX_FPS):
            return frames
        step = max(1, round(fps * step_seconds))
        index = 0
        while len(frames) < max_frames and time.monotonic() < deadline:
            if not cap.grab():
                break
            if index % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                frames.append((index / fps, _to_image(frame)))
            index += 1
    finally:
        cap.release()
    return frames
