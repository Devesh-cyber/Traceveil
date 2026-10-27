"""Pre-flight check for one website. Downloads NO pictures and compares nothing.

Plain run: may we read the home page (robots.txt, blocking, errors), how many pages would the crawler
line up, and how many picture addresses are on the home page. It prints counts, not addresses.

    python -m dev.probe_site example.org

Finding out why a particular picture is not found:
    1. On the website, right-click the picture and choose "Copy image address".
    2. Pass any distinctive part of that address (for example the file name):

    python -m dev.probe_site example.org --find 8d3f21ab.jpg

It reads the pages the crawler would read and says, for that picture, one of:
  * found within the limit        -> the crawler would compare it
  * found but beyond the limit    -> raise SCAN_MAX_IMAGES_PER_PAGE
  * not in any page we would read -> it is on a page outside the budget (SCAN_MAX_PAGES_PER_DOMAIN),
                                     or built by JavaScript / "load more" (we do not run scripts)

Add --pages to also print the page addresses the crawler would read.
It uses the same strict fetcher as a real scan, so a site that blocks us here will block us in a scan too.
Never try to get around a block.
"""

import argparse
import asyncio
import sys
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.crawler.assets import extract_image_urls
from app.crawler.domains import normalise_domain
from app.crawler.fetcher import Fetcher
from app.crawler.pages import discover_pages


async def main(raw: str, find: str | None = None, show_pages: bool = False) -> int:
    try:
        domain = normalise_domain(raw)
    except ValueError as exc:
        print(f"Not a usable domain: {exc}")
        return 2
    settings = get_settings()
    start = f"https://{domain}/"
    limit = settings.scan_max_images_per_page
    print(f"Site: {domain}")
    print(f"Crawler contact sent to the site: {settings.crawler_contact}")
    if settings.crawler_contact == "contact-not-configured":
        print("WARNING: set CRAWLER_CONTACT in .env to a real address before scanning a real site.")
    async with Fetcher() as fetcher:
        found = await discover_pages(fetcher, start, settings.scan_max_pages_per_domain, 2_000_000)
        home = found.homepage
        print(f"Home page: {home.status.value}" + (f" ({home.detail})" if home.detail else ""))
        if not home.ok:
            print("Result: we cannot read this site. A scan would report it as NOT searched. Do not work around it.")
            return 1
        print(f"Pages the crawler would read (budget {settings.scan_max_pages_per_domain}): {len(found.pages)}")
        if show_pages:
            for number, url in enumerate(found.pages, start=1):
                print(f"  page {number}: {urlsplit(url).path or '/'}")

        html = home.body.decode("utf-8", errors="replace")
        images = extract_image_urls(html, start, limit)
        print(f"Picture addresses on the home page (limit {limit} per page): {len(images)}")
        if not images:
            print("Note: no pictures in the plain HTML. The site may need JavaScript or an age gate we do not pass.")
        if not find:
            return 0

        needle = find.strip().lower()
        hit = False
        for number, url in enumerate(found.pages, start=1):
            page = home if number == 1 else await fetcher.fetch(
                url, accept=("text/html", "application/xhtml"), max_bytes=2_000_000, referer=found.pages[0]
            )
            if not page.ok:
                print(f"  page {number}: could not be read ({page.status.value})")
                continue
            text = page.body.decode("utf-8", errors="replace")
            everything = extract_image_urls(text, url, 100_000)
            tags = len(BeautifulSoup(text, "html.parser").find_all("img"))
            position = next((i for i, u in enumerate(everything, start=1) if needle in u.lower()), None)
            line = f"  page {number}: {tags} <img> tags, {len(everything)} distinct picture addresses"
            if position is None:
                print(line + ", no match for your text")
            elif position <= limit:
                print(line + f", YOUR PICTURE IS #{position}: within the limit, the crawler would compare it")
                hit = True
            else:
                print(line + f", YOUR PICTURE IS #{position}: BEYOND the limit of {limit}. Raise SCAN_MAX_IMAGES_PER_PAGE to at least {position}")
                hit = True
        if not hit:
            print("Your picture is in none of the pages we would read. Either it is on a page outside the budget "
                  "(raise SCAN_MAX_PAGES_PER_DOMAIN, or it may be on page 2/3 of a list), or the page builds its "
                  "pictures with JavaScript ('load more', infinite scroll), which we do not run.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("domain")
    parser.add_argument("--find", help="part of a picture's address, to see why it is or is not found")
    parser.add_argument("--pages", action="store_true", help="print the page addresses the crawler would read")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.domain, args.find, args.pages)))