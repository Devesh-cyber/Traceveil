"""Feature-point matching with a geometry check.

Idea: find distinctive spots (corners, blobs) in both images and pair up the ones that
look the same. In a real copy, the pairs also agree on ONE consistent transformation
(shift + scale + rotation), because the whole picture moved together. Unrelated images
can have similar-looking spots, but they will not agree on one transformation.
The number of pairs that agree is called the 'inlier count'.

Steps: SIFT finds the spots -> Lowe's ratio test keeps only clear pairs ->
RANSAC searches for the transformation most pairs agree on.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

NORM_MAX_SIDE = 512     # both images are shrunk to at most this size first
MAX_FEATURES = 700
RATIO = 0.75            # a pair counts only if its best partner is clearly better than the 2nd best
MIN_KEYPOINTS = 8
MIN_GOOD_PAIRS = 8
RANSAC_TOLERANCE = 4.0  # pixels
SCALE_RANGE = (0.2, 5.0)  # reject absurd transformations


@dataclass
class Features:
    points: np.ndarray       # (N, 2) positions
    descriptors: np.ndarray  # (N, 128) description of each spot


@dataclass
class GeoResult:
    good_pairs: int = 0
    inliers: int = 0
    inlier_ratio: float = 0.0
    scale: float = 0.0


def available() -> bool:
    return hasattr(cv2, "SIFT_create")


def to_gray(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.uint8)


def _normalise(gray: np.ndarray) -> np.ndarray:
    height, width = gray.shape[:2]
    scale = NORM_MAX_SIDE / max(height, width)
    if scale < 1.0:
        gray = cv2.resize(
            gray, (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)


def extract_features(gray: np.ndarray) -> Features | None:
    """Find feature points. Returns None if the image has too few (flat or tiny)."""
    if not available() or gray is None or gray.size == 0:
        return None
    try:
        sift = cv2.SIFT_create(nfeatures=MAX_FEATURES)
        keypoints, descriptors = sift.detectAndCompute(_normalise(gray), None)
    except cv2.error:
        return None
    if descriptors is None or len(keypoints) < MIN_KEYPOINTS:
        return None
    points = np.float32([k.pt for k in keypoints])
    return Features(points=points, descriptors=descriptors.astype(np.float32))


def match_features(reference: Features | None, candidate: Features | None) -> GeoResult:
    if reference is None or candidate is None:
        return GeoResult()
    try:
        pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(
            reference.descriptors, candidate.descriptors, k=2
        )
        good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < RATIO * p[1].distance]
        if len(good) < MIN_GOOD_PAIRS:
            return GeoResult(good_pairs=len(good))

        src = np.float32([reference.points[m.queryIdx] for m in good]).reshape(-1, 1, 2)
        dst = np.float32([candidate.points[m.trainIdx] for m in good]).reshape(-1, 1, 2)
        matrix, mask = cv2.estimateAffinePartial2D(
            src, dst, method=cv2.RANSAC, ransacReprojThreshold=RANSAC_TOLERANCE
        )
    except cv2.error:
        return GeoResult()
    if matrix is None or mask is None:
        return GeoResult(good_pairs=len(good))

    scale = float(np.sqrt(abs(np.linalg.det(matrix[:, :2]))))
    if not (SCALE_RANGE[0] <= scale <= SCALE_RANGE[1]):
        return GeoResult(good_pairs=len(good), scale=scale)

    inliers = int(mask.sum())
    return GeoResult(
        good_pairs=len(good),
        inliers=inliers,
        inlier_ratio=round(inliers / len(good), 4),
        scale=round(scale, 3),
    )