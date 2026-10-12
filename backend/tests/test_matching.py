import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from app.matching.fingerprint import (
    ImageError,
    build_reference,
    center_crop,
    load_image,
)
from app.matching.geometry import GeoResult
from app.matching.tiers import Thresholds, classify_by_geometry
from app.matching.verify import evaluate_candidate
from app.models.match import MatchKind
from tests.factories import make_photo, recompress, to_bytes

SEEDS = [1, 2, 3, 4, 5, 6]


@pytest.fixture(params=SEEDS)
def photo(request):
    return make_photo(request.param)


def _decide(original: Image.Image, copy: Image.Image):
    return evaluate_candidate(build_reference(original), copy)


# ---------- copies that must be found ----------

def test_identical_image_is_exact(photo):
    decision = _decide(photo, photo.copy())
    assert decision is not None and decision.kind == MatchKind.exact


def test_small_thumbnail_is_found(photo):
    decision = _decide(photo, photo.resize((160, 120)))
    assert decision is not None and decision.kind == MatchKind.exact


def test_heavy_recompression_is_found(photo):
    decision = _decide(photo, recompress(photo, 30))
    assert decision is not None


def test_mirrored_copy_is_found(photo):
    decision = _decide(photo, ImageOps.mirror(photo))
    assert decision is not None
    assert decision.kind == MatchKind.near_duplicate
    assert decision.variant == "mirrored"


def test_brightness_and_contrast_change_is_found(photo):
    changed = ImageEnhance.Contrast(ImageEnhance.Brightness(photo).enhance(1.25)).enhance(1.2)
    assert _decide(photo, changed) is not None


def test_watermark_is_found(photo):
    marked = photo.copy()
    draw = ImageDraw.Draw(marked)
    draw.rectangle([10, 10, 170, 50], fill=(255, 255, 255))
    draw.text((20, 22), "SOME WEBSITE", fill=(0, 0, 0))
    assert _decide(photo, marked) is not None


def test_corner_crop_is_found_by_feature_points(photo):
    width, height = photo.size
    cropped = photo.crop((0, 0, int(width * 0.6), int(height * 0.6)))
    decision = _decide(photo, cropped)
    assert decision is not None
    assert decision.kind in (MatchKind.structural, MatchKind.probable)
    assert decision.signals["method"] == "sift_ransac"


def test_centre_crop_is_found(photo):
    assert _decide(photo, center_crop(photo, 1.0)) is not None


# ---------- things that must NOT match ----------

def test_unrelated_images_do_not_match():
    reference = build_reference(make_photo(100))
    for seed in range(200, 215):
        assert evaluate_candidate(reference, make_photo(seed)) is None, f"false alarm: seed {seed}"


def test_flat_image_is_ignored(photo):
    flat = Image.new("RGB", (300, 300), (128, 128, 128))
    assert _decide(photo, flat) is None


def test_tiny_image_is_ignored(photo):
    assert _decide(photo, photo.resize((30, 30))) is None


# ---------- decision rules (no images needed) ----------

def test_geometry_rules_need_enough_agreeing_pairs():
    t = Thresholds()
    strong = GeoResult(good_pairs=40, inliers=30, inlier_ratio=0.75, scale=1.0)
    medium = GeoResult(good_pairs=30, inliers=12, inlier_ratio=0.4, scale=1.0)
    weak = GeoResult(good_pairs=30, inliers=6, inlier_ratio=0.2, scale=1.0)
    scattered = GeoResult(good_pairs=100, inliers=20, inlier_ratio=0.2, scale=1.0)

    assert classify_by_geometry(strong, "original", t).kind == MatchKind.structural
    assert classify_by_geometry(medium, "original", t).kind == MatchKind.probable
    assert classify_by_geometry(weak, "original", t) is None
    assert classify_by_geometry(scattered, "original", t) is None  # many pairs, but they disagree


def test_decisions_carry_their_evidence_not_a_made_up_percentage(photo):
    decision = _decide(photo, photo.copy())
    assert decision.signals["method"] == "fingerprint"
    assert "phash" in decision.signals
    assert "confidence" not in decision.signals


# ---------- loading images safely ----------

def test_load_image_accepts_png_and_jpeg(photo):
    assert load_image(to_bytes(photo, "PNG")).size == photo.size
    assert load_image(to_bytes(photo, "JPEG")).size == photo.size


def test_load_image_rejects_non_images():
    with pytest.raises(ImageError):
        load_image(b"this is not an image")


def test_load_image_rejects_unsupported_format(photo):
    with pytest.raises(ImageError):
        load_image(to_bytes(photo, "BMP"))


def test_load_image_rejects_huge_images():
    huge = Image.new("1", (7000, 7000))  # 49 million pixels, but tiny when saved as PNG
    with pytest.raises(ImageError):
        load_image(to_bytes(huge, "PNG"))