"""A harmless fake website for testing the crawler on your own computer.

Usage (from the backend folder):
    python -m dev.fake_site path/to/an/ordinary/test/photo.jpg

Use an ordinary photo (a landscape, a pet, an object). NEVER use a private or intimate image.

It builds a small site in dev/site/ and serves it at http://localhost:8001 :
  * pages under /video/ that contain copies of your photo (resized, mirrored, cropped)
  * pages with unrelated pictures
  * a robots.txt that forbids /private/, where one more copy is hidden
  * a sitemap.xml
"""

import argparse
import random
import shutil
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

SITE_DIR = Path(__file__).parent / "site"


def unrelated_picture(seed: int, size=(480, 360)) -> Image.Image:
    rng = random.Random(seed)
    image = Image.new("RGB", size, tuple(rng.randint(30, 220) for _ in range(3)))
    draw = ImageDraw.Draw(image)
    for _ in range(40):
        x0, y0 = rng.randint(0, size[0]), rng.randint(0, size[1])
        box = [x0, y0, x0 + rng.randint(20, 150), y0 + rng.randint(20, 120)]
        colour = tuple(rng.randint(0, 255) for _ in range(3))
        draw.ellipse(box, fill=colour) if rng.random() < 0.5 else draw.rectangle(box, fill=colour)
    return image.filter(ImageFilter.GaussianBlur(0.8))


def page(title: str, images: list[str], body: str = "") -> str:
    tags = "".join(f'<img src="{src}" width="320" alt="">' for src in images)
    return (
        f'<html><head><title>{title}</title>'
        f'<meta property="og:image" content="{images[0] if images else ""}"></head>'
        f"<body><h1>{title}</h1>{body}{tags}</body></html>"
    )


def build_site(photo_path: Path, port: int) -> None:
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    (SITE_DIR / "img").mkdir(parents=True)
    (SITE_DIR / "video").mkdir()
    (SITE_DIR / "private").mkdir()

    photo = Image.open(photo_path).convert("RGB")
    width, height = photo.size
    side = min(width, height)
    copies = {
        "copy_resized.jpg": photo.resize((max(64, width // 3), max(64, height // 3))),
        "copy_mirrored.jpg": ImageOps.mirror(photo),
        "copy_square_crop.jpg": photo.crop(
            ((width - side) // 2, (height - side) // 2, (width + side) // 2, (height + side) // 2)
        ),
        "copy_hidden.jpg": photo.copy(),
    }
    for name, image in copies.items():
        image.save(SITE_DIR / "img" / name, quality=85)
    for number in range(1, 7):
        unrelated_picture(number).save(SITE_DIR / "img" / f"other_{number}.jpg", quality=85)

    layout = {
        "video/1.html": ["/img/copy_resized.jpg", "/img/other_1.jpg"],
        "video/2.html": ["/img/other_2.jpg", "/img/copy_mirrored.jpg"],
        "video/3.html": ["/img/copy_square_crop.jpg"],
        "video/4.html": ["/img/other_3.jpg", "/img/other_4.jpg"],
        "video/5.html": ["/img/other_5.jpg", "/img/other_6.jpg"],
        "private/secret.html": ["/img/copy_hidden.jpg"],
    }
    for relative, images in layout.items():
        (SITE_DIR / relative).write_text(page(f"Page {relative}", images), encoding="utf-8")

    links = "".join(f'<li><a href="/{p}">{p}</a></li>' for p in layout if p.startswith("video"))
    (SITE_DIR / "index.html").write_text(
        page("Fake test site", ["/img/other_1.jpg"], f"<ul>{links}</ul>"), encoding="utf-8"
    )
    base = f"http://localhost:{port}"
    (SITE_DIR / "robots.txt").write_text(
        f"User-agent: *\nDisallow: /private/\nSitemap: {base}/sitemap.xml\n"
    )
    urls = "".join(f"<url><loc>{base}/{p}</loc></url>" for p in layout if p.startswith("video"))
    (SITE_DIR / "sitemap.xml").write_text(
        f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("photo", type=Path, help="an ordinary test photo")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if not args.photo.is_file():
        sys.exit(f"Cannot find the photo: {args.photo}")

    build_site(args.photo, args.port)
    handler = partial(SimpleHTTPRequestHandler, directory=str(SITE_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"Fake site is running at http://localhost:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()