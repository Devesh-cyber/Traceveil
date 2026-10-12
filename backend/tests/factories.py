"""Makes repeatable test pictures. The same seed always gives the same picture;
different seeds give unrelated pictures."""

import io
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def make_photo(seed: int, size: tuple[int, int] = (480, 360)) -> Image.Image:
    rng = random.Random(seed)
    width, height = size

    # smooth coloured background
    x = np.linspace(0, 1, width)[None, :, None]
    y = np.linspace(0, 1, height)[:, None, None]
    c1, c2, c3 = (np.array([rng.randint(20, 235) for _ in range(3)]) for _ in range(3))
    background = c1 * x + c2 * y + c3 * (1 - x) * (1 - y)
    image = Image.fromarray(np.clip(background / 1.6, 0, 255).astype("uint8"))

    # many random shapes give the picture structure and corners
    draw = ImageDraw.Draw(image)
    for _ in range(45):
        x0, y0 = rng.randint(0, width), rng.randint(0, height)
        x1, y1 = x0 + rng.randint(15, 140), y0 + rng.randint(15, 120)
        colour = tuple(rng.randint(0, 255) for _ in range(3))
        shape = rng.choice(["ellipse", "rectangle", "line"])
        if shape == "ellipse":
            draw.ellipse([x0, y0, x1, y1], fill=colour)
        elif shape == "rectangle":
            draw.rectangle([x0, y0, x1, y1], fill=colour)
        else:
            draw.line([x0, y0, x1, y1], fill=colour, width=rng.randint(2, 8))

    image = image.filter(ImageFilter.GaussianBlur(0.8))
    noise = np.random.RandomState(seed).normal(0, 4, (height, width, 3))
    pixels = np.clip(np.asarray(image, dtype=float) + noise, 0, 255).astype("uint8")
    return Image.fromarray(pixels)


def to_bytes(image: Image.Image, fmt: str = "PNG", **kwargs) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def recompress(image: Image.Image, quality: int) -> Image.Image:
    return Image.open(io.BytesIO(to_bytes(image, "JPEG", quality=quality))).convert("RGB")