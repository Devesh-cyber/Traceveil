"""Quick manual check of the fetcher against the fake site.

Run the fake site in one terminal, then in another (from the backend folder):
    NETGUARD_ALLOW_LOOPBACK=true python -m dev.try_fetch
"""

import asyncio

from app.crawler.fetcher import Fetcher

BASE = "http://localhost:8001"
URLS = [
    f"{BASE}/",
    f"{BASE}/video/1.html",
    f"{BASE}/img/copy_resized.jpg",
    f"{BASE}/private/secret.html",   # forbidden by robots.txt
    f"{BASE}/nothing-here.html",
    "http://169.254.169.254/latest/meta-data/",  # must always be refused
]


async def main() -> None:
    async with Fetcher(min_delay=0) as fetcher:
        print("sitemaps announced:", await fetcher.sitemaps_for(BASE))
        for url in URLS:
            result = await fetcher.fetch(url, max_bytes=3_000_000)
            print(f"{result.status.value:12} {result.http_status or '-':>4}  {url}  {result.detail or ''}")


asyncio.run(main())