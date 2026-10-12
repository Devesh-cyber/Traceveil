"""Image loading and fingerprints.

A fingerprint is a short code (64 bits) describing what an image looks like. Two images
that look alike have codes that differ in only a few bits. We use three kinds, because
each one is fooled by different edits:

  * pHash  - based on the image's overall shapes. Good against resizing and recompression.
  * dHash  - based on brightness changes between neighbouring pixels.
  * aHash  - based on which areas are lighter or darker than the average.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import imagehash
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PIXELS = 40_000_000
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}

# Centre crops we also try, because leaked copies are often cropped to a different shape.
CROP_ASPECTS = {
    "crop_16x9": 16 / 9,
    "crop_4x3": 4 / 3,
    "crop_1x1": 1.0,
    "crop_9x16": 9 / 16,
}


class ImageError(ValueError):
    """The bytes are not a usable image (wrong type, corrupt, or too large)."""


@dataclass(frozen=True)
class Fingerprint:
    phash: imagehash.ImageHash
    dhash: imagehash.ImageHash
    ahash: imagehash.ImageHash


@dataclass(frozen=True)
class HashDistances:
    phash: int
    dhash: int
    ahash: int

    @property
    def average(self) -> float:
        return (self.phash + self.dhash + self.ahash) / 3


@dataclass
class Reference:
    """The user's image, prepared once so every candidate can be compared quickly."""

    variants: dict[str, Fingerprint]
    # Feature points (see geometry.py) for the original and the mirrored image.
    features: dict[str, object] = field(default_factory=dict)


def load_image(data: bytes) -> Image.Image:
    """Open image bytes safely. Raises ImageError for anything we do not accept."""
    try:
        image = Image.open(io.BytesIO(data))
        if image.format not in ALLOWED_FORMATS:
            raise ImageError(f"Unsupported image type: {image.format}")
        width, height = image.size  # read from the header, nothing decoded yet
        if width * height > MAX_PIXELS:
            raise ImageError("Image is too large.")
        image = ImageOps.exif_transpose(image)  # apply the camera's rotation
        return image.convert("RGB")
    except ImageError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError) as exc:
        raise ImageError("File is not a readable image.") from exc


def compute_fingerprint(image: Image.Image) -> Fingerprint:
    return Fingerprint(
        phash=imagehash.phash(image),
        dhash=imagehash.dhash(image),
        ahash=imagehash.average_hash(image),
    )


def hash_distances(a: Fingerprint, b: Fingerprint) -> HashDistances:
    """How many bits differ for each kind of fingerprint (0 means identical)."""
    return HashDistances(
        phash=int(a.phash - b.phash),
        dhash=int(a.dhash - b.dhash),
        ahash=int(a.ahash - b.ahash),
    )


def center_crop(image: Image.Image, aspect: float) -> Image.Image:
    """Crop the middle of the image to a width/height ratio of `aspect`."""
    width, height = image.size
    if width / height > aspect:  # too wide: trim the sides
        new_width = round(height * aspect)
        left = (width - new_width) // 2
        return image.crop((left, 0, left + new_width, height))
    new_height = round(width / aspect)  # too tall: trim top and bottom
    top = (height - new_height) // 2
    return image.crop((0, top, width, top + new_height))


def image_variants(image: Image.Image) -> dict[str, Image.Image]:
    """The original plus the versions a copy might have been turned into."""
    variants = {"original": image, "mirrored": ImageOps.mirror(image)}
    own_aspect = image.width / image.height
    for name, aspect in CROP_ASPECTS.items():
        if abs(aspect - own_aspect) / own_aspect > 0.03:  # skip crops that change nothing
            variants[name] = center_crop(image, aspect)
    return variants


def build_reference(image: Image.Image) -> Reference:
    """Prepare the user's image: fingerprints for every variant, features for two."""
    from app.matching import geometry  # imported here to keep this module light

    variants = image_variants(image)
    reference = Reference(
        variants={name: compute_fingerprint(img) for name, img in variants.items()}
    )
    for name in ("original", "mirrored"):
        features = geometry.extract_features(geometry.to_gray(variants[name]))
        if features is not None:
            reference.features[name] = features
    return reference