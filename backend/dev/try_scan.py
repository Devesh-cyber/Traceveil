"""Run a real scan against the fake site and print what the crawler found.

1. In one terminal:   python -m dev.fake_site path/to/test/photo.jpg
2. In another one:    NETGUARD_ALLOW_LOOPBACK=true python -m dev.try_scan path/to/the/same/photo.jpg
"""

import asyncio
import sys
from pathlib import Path

from app.crawler.domains import DomainEntry
from app.crawler.fetcher import Fetcher
from app.crawler.runner import ScanLimits, ScanProgress, run_scan
from app.matching.fingerprint import build_reference, load_image


async def main(photo: Path) -> None:
    reference = build_reference(load_image(photo.read_bytes()))
    site = DomainEntry(domain="localhost", base_url="http://localhost:8001")
    progress = ScanProgress()

    async with Fetcher(min_delay=0) as fetcher:
        summary = await run_scan([site], reference, fetcher, ScanLimits(), progress=progress)

    for outcome in summary.outcomes:
        print(f"\nSITE {outcome.domain}: {outcome.status.value}  "
              f"pages={outcome.pages_scanned} images={outcome.images_compared}  {outcome.detail or ''}")
        for found in outcome.matches:
            signals = found.decision.signals
            print(f"  MATCH {found.decision.kind.value:15} variant={found.decision.variant:10} "
                  f"method={signals['method']}")
            print(f"        page : {found.source_page_url}")
            print(f"        image: {found.image_url}")
    print("\ncoverage:", summary.coverage(), "| truncated:", summary.truncated)


if len(sys.argv) != 2:
    sys.exit(__doc__)
asyncio.run(main(Path(sys.argv[1])))