"""A tile that lists a placeholder, a real picture and several sizes must count once against the page limit."""

from app.crawler.assets import extract_image_urls


def _tile(n: int) -> str:
    return (
        f'<div><img src="/ph/blank.png" data-src="/pics/real_{n}.jpg" '
        f'srcset="/pics/small_{n}.jpg 160w, /pics/big_{n}.jpg 640w"></div>'
    )


def test_every_tile_counts_once_so_lower_pictures_are_reached():
    page = "<html><body>" + "".join(_tile(n) for n in range(1, 61)) + "</body></html>"
    urls = extract_image_urls(page, "https://site.test/", limit=60)
    assert len(urls) == 60
    assert urls[35].endswith("big_36.jpg")  # the 36th tile is reached
    assert not any("blank.png" in u for u in urls)  # the placeholder is not used when a real picture exists


def test_real_picture_preferred_over_placeholder_src_without_srcset():
    page = '<img src="/ph/blank.png" data-src="/pics/a.jpg"><img src="/pics/b.jpg">'
    assert extract_image_urls(page, "https://site.test/", 10) == [
        "https://site.test/pics/a.jpg", "https://site.test/pics/b.jpg"
    ]


def test_src_is_used_when_nothing_else_is_given():
    assert extract_image_urls('<img src="x.jpg">', "https://site.test/a/", 5) == ["https://site.test/a/x.jpg"]