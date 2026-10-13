"""Compare a web picture with ALL the keyframes of the user's video."""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image

from app.matching import geometry
from app.matching.fingerprint import Reference, compute_fingerprint, hash_distances
from app.matching.tiers import (
    DEFAULT, MatchDecision, Thresholds, classify_by_geometry, classify_by_hash,
)
from app.models.match import MatchKind

KIND_RANK = {MatchKind.exact: 0, MatchKind.near_duplicate: 1, MatchKind.structural: 2, MatchKind.probable: 3}
GEOMETRY_TOP_K = 3  # the expensive feature-point test is run only for the closest few keyframes


@dataclass
class KeyframeRef:
    time_seconds: float
    reference: Reference


@dataclass
class FrameAnalysis:
    decision: MatchDecision | None = None
    hits: list[tuple[float, MatchDecision]] = field(default_factory=list)  # (keyframe time, decision)


class VideoMatcher:
    def __init__(self, keyframes: list[KeyframeRef], thresholds: Thresholds = DEFAULT) -> None:
        self.keyframes = keyframes
        self.t = thresholds

    def analyse(self, image: Image.Image) -> FrameAnalysis:
        t = self.t
        if min(image.size) < t.min_side_px:
            return FrameAnalysis()
        gray = geometry.to_gray(image)
        if float(gray.std()) < t.min_gray_std:
            return FrameAnalysis()

        candidate_fp = compute_fingerprint(image)
        scored = []
        for keyframe in self.keyframes:
            best_name, best = None, None
            for name, reference_fp in keyframe.reference.variants.items():
                distances = hash_distances(reference_fp, candidate_fp)
                if best is None or distances.average < best.average:
                    best_name, best = name, distances
            scored.append((best.average, keyframe, best_name, best))
        scored.sort(key=lambda item: item[0])

        hits: dict[float, MatchDecision] = {}
        for _, keyframe, variant, distances in scored:  # cheap test for every keyframe
            decision = classify_by_hash(distances, variant, t)
            if decision:
                hits[keyframe.time_seconds] = decision

        if geometry.available():  # the careful test, only for the few closest keyframes
            features = None
            for _, keyframe, _, _ in scored[:GEOMETRY_TOP_K]:
                if keyframe.time_seconds in hits:
                    continue
                if features is None:
                    features = geometry.extract_features(gray)
                best_result, best_name = None, None
                for name, reference_features in keyframe.reference.features.items():
                    result = geometry.match_features(reference_features, features)
                    if best_result is None or result.inliers > best_result.inliers:
                        best_result, best_name = result, name
                if best_result is not None:
                    decision = classify_by_geometry(best_result, best_name, t)
                    if decision:
                        hits[keyframe.time_seconds] = decision

        if not hits:
            return FrameAnalysis()
        ordered = sorted(hits.items(), key=lambda item: (KIND_RANK[item[1].kind], item[0]))
        return FrameAnalysis(decision=ordered[0][1], hits=sorted(hits.items()))

    def best_keyframe(self, image: Image.Image) -> tuple[float, MatchDecision] | None:
        """The keyframe this picture matches best, as (time in the user's video, decision)."""
        analysis = self.analyse(image)
        if not analysis.hits:
            return None
        return min(analysis.hits, key=lambda item: (KIND_RANK[item[1].kind], item[0]))

    def compare(self, image: Image.Image) -> MatchDecision | None:
        """Same shape as the image matcher, so the crawler needs no changes."""
        analysis = self.analyse(image)
        if analysis.decision is None:
            return None
        times = [round(time, 2) for time, _ in analysis.hits]
        analysis.decision.signals = {
            **analysis.decision.signals,
            "reference": "video",
            "keyframes_matched": len(times),
            "keyframe_times": times,
        }
        return analysis.decision
