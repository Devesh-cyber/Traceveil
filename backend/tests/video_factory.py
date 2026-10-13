"""Makes small real MP4 files for tests: scenes built from the repeatable test pictures,
each with a slow zoom so that the video really changes over time."""

from pathlib import Path

import cv2
import numpy as np

from tests.factories import make_photo

SCENE_SECONDS = 3


def scene_frames(seed: int, fps: int, seconds: float, size: tuple[int, int]):
    base = np.asarray(make_photo(seed, (480, 360)).convert("RGB"))
    height, width = base.shape[:2]
    count = int(fps * seconds)
    for i in range(count):
        zoom = 1.0 + 0.12 * (i / max(1, count - 1))
        crop_w, crop_h = int(width / zoom), int(height / zoom)
        left, top = (width - crop_w) // 2, (height - crop_h) // 2
        crop = base[top:top + crop_h, left:left + crop_w]
        yield cv2.cvtColor(cv2.resize(crop, size), cv2.COLOR_RGB2BGR)


def make_video(
    path: Path,
    scene_seeds: list[int],
    *,
    fps: int = 10,
    size: tuple[int, int] = (320, 240),
    black_intro_seconds: float = 0,
    scene_seconds: float = SCENE_SECONDS,
) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    assert writer.isOpened(), "this OpenCV build cannot write mp4 files"
    try:
        for _ in range(int(fps * black_intro_seconds)):
            writer.write(np.zeros((size[1], size[0], 3), dtype=np.uint8))
        for seed in scene_seeds:
            for frame in scene_frames(seed, fps, scene_seconds, size):
                writer.write(frame)
    finally:
        writer.release()
    return path
