"""Try the whole flow through the real API: register, upload, scan, watch, read results.

Needs the API, the worker and the fake site running (see the Step 8 instructions).
    python -m dev.try_api path/to/the/same/test/photo.jpg
"""

import sys
import time
import uuid
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000/api"


def main(photo: Path) -> None:
    with httpx.Client(base_url=BASE, timeout=30) as api:
        email = f"try-{uuid.uuid4().hex[:6]}@example.org"
        password = "correct-horse-battery"
        api.post("/auth/register", json={"email": email, "password": password}).raise_for_status()
        token = api.post("/auth/login", data={"username": email, "password": password}).json()["access_token"]
        api.headers["Authorization"] = f"Bearer {token}"

        case = api.post("/cases", json={"title": "try_api demo"}).json()
        upload = api.post(f"/cases/{case['id']}/evidence", files={"file": (photo.name, photo.read_bytes())})
        upload.raise_for_status()
        evidence = upload.json()
        print("uploaded, sha256:", evidence["sha256"][:16], "...")

        scan = api.post(f"/cases/{case['id']}/scans", json={"evidence_id": evidence["id"]}).json()
        print("scan queued:", scan["id"])
        while True:
            scan = api.get(f"/scans/{scan['id']}").json()
            counters = {k: v for k, v in scan["stats"].items() if k != "coverage"}
            print(f"  {scan['status']:10} {counters}")
            if scan["status"] not in ("queued", "running"):
                break
            time.sleep(1)

        print("\ncoverage:", scan["stats"].get("coverage"), "| error:", scan["error"])
        for site in api.get(f"/scans/{scan['id']}/domains").json():
            print(f"  site  {site['domain']:22} {site['status']:12} pages={site['pages_scanned']}  {site['detail'] or ''}")
        for match in api.get(f"/scans/{scan['id']}/matches").json():
            print(f"  match {match['kind']:15} {match['review_status']:10} {match['image_url']}")

        api.delete(f"/cases/{case['id']}")  # clean up: removes the case, its rows and its files
        print("\ncase deleted")


if len(sys.argv) != 2:
    sys.exit(__doc__)
main(Path(sys.argv[1]))