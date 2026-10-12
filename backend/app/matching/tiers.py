"""The rules that turn raw measurements into a match label.

We deliberately do NOT invent a '97% confidence' number. A decision carries its label
and the measurements behind it, so a person can see why it was flagged.
All limits live in `Thresholds`, so they can be tuned in one place once we have real data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.matching.fingerprint import HashDistances
from app.matching.geometry import GeoResult
from app.models.match import MatchKind


@dataclass(frozen=True)
class Thresholds:
    hash_average_max: float = 4.0     # average bit difference across the 3 fingerprints
    hash_phash_max: int = 6           # pHash alone must also be this close
    geo_structural_inliers: int = 15  # agreeing feature pairs for a 'structural' match
    geo_probable_inliers: int = 10    # agreeing feature pairs for a 'probable' match
    geo_min_inlier_ratio: float = 0.30
    min_side_px: int = 48             # smaller images (icons, tracking pixels) are ignored
    min_gray_std: float = 8.0         # flatter images carry too little information


DEFAULT = Thresholds()


@dataclass
class MatchDecision:
    kind: MatchKind
    variant: str                       # which version of the reference matched
    signals: dict = field(default_factory=dict)


def classify_by_hash(
    distances: HashDistances, variant: str, t: Thresholds = DEFAULT
) -> MatchDecision | None:
    if distances.average <= t.hash_average_max and distances.phash <= t.hash_phash_max:
        kind = MatchKind.exact if variant == "original" else MatchKind.near_duplicate
        return MatchDecision(
            kind=kind,
            variant=variant,
            signals={
                "method": "fingerprint",
                "phash": distances.phash,
                "dhash": distances.dhash,
                "ahash": distances.ahash,
                "average": round(distances.average, 2),
            },
        )
    return None


def classify_by_geometry(
    result: GeoResult, variant: str, t: Thresholds = DEFAULT
) -> MatchDecision | None:
    if result.inlier_ratio < t.geo_min_inlier_ratio:
        return None
    if result.inliers >= t.geo_structural_inliers:
        kind = MatchKind.structural
    elif result.inliers >= t.geo_probable_inliers:
        kind = MatchKind.probable
    else:
        return None
    return MatchDecision(
        kind=kind,
        variant=variant,
        signals={
            "method": "sift_ransac",
            "inliers": result.inliers,
            "good_pairs": result.good_pairs,
            "inlier_ratio": result.inlier_ratio,
            "scale": result.scale,
        },
    )