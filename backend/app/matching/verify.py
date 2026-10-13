"""Compare one candidate image against the prepared reference."""

from __future__ import annotations

from PIL import Image

from app.matching import geometry
from app.matching.fingerprint import Reference, compute_fingerprint, hash_distances
from app.matching.tiers import (
    DEFAULT,
    MatchDecision,
    Thresholds,
    classify_by_geometry,
    classify_by_hash,
)


def evaluate_candidate(
    reference: Reference, candidate: Image.Image, t: Thresholds = DEFAULT
) -> MatchDecision | None:
    """Return a MatchDecision if the candidate looks like a copy, otherwise None.

    Cheap check first (fingerprints), expensive check second (feature points).
    """
    if min(candidate.size) < t.min_side_px:
        return None
    gray = geometry.to_gray(candidate)
    if float(gray.std()) < t.min_gray_std:  # almost one flat colour
        return None

    # 1. Fingerprints against every variant (original, mirrored, crops): pick the closest.
    candidate_fp = compute_fingerprint(candidate)
    best_variant, best_distances = None, None
    for name, reference_fp in reference.variants.items():
        distances = hash_distances(reference_fp, candidate_fp)
        if best_distances is None or distances.average < best_distances.average:
            best_variant, best_distances = name, distances
    decision = classify_by_hash(best_distances, best_variant, t)
    if decision:
        return decision

    # 2. Feature points with a geometry check (survives cropping, watermarks, overlays).
    if not geometry.available():
        return None
    candidate_features = geometry.extract_features(gray)
    best_result, best_name = None, None
    for name, reference_features in reference.features.items():
        result = geometry.match_features(reference_features, candidate_features)
        if best_result is None or result.inliers > best_result.inliers:
            best_result, best_name = result, name
    if best_result is None:
        return None
    return classify_by_geometry(best_result, best_name, t)

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
