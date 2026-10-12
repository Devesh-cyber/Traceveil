"""The list of websites we search. It lives in data/domains.csv and is researched by us."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.config import ROOT_DIR

DEFAULT_PATH = ROOT_DIR / "backend" / "data" / "domains.csv"
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True)
class DomainEntry:
    domain: str
    category: str = ""
    media_types: tuple[str, ...] = ("image", "video")
    notes: str = ""
    last_checked: str = ""  # a date written as YYYY-MM-DD
    base_url: str = ""      # development only: e.g. http://localhost:8001 for a fake site

    @property
    def start_url(self) -> str:
        return self.base_url or f"https://{self.domain}/"


def normalise_domain(raw: str) -> str:
    """'HTTPS://Example.ORG/some/page' -> 'example.org'. Raises ValueError if unusable."""
    value = (raw or "").strip().lower()
    value = re.sub(r"^[a-z][a-z0-9+.-]*://", "", value)  # drop http:// and similar
    value = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].rstrip(".")
    if not value:
        raise ValueError("empty domain")
    if "@" in value or ":" in value:
        raise ValueError("a domain must not contain a user name or a port")
    labels = value.split(".")
    if len(labels) < 2 or len(value) > 253:
        raise ValueError("not a valid domain name")
    if not all(_LABEL.match(label) for label in labels):
        raise ValueError("not a valid domain name")
    if all(label.isdigit() for label in labels):
        raise ValueError("IP addresses are not accepted")
    return value


def load_domains(path: Path = DEFAULT_PATH) -> tuple[list[DomainEntry], list[str]]:
    """Read the CSV. Returns (entries, problems). A bad row never stops the others."""
    entries: list[DomainEntry] = []
    problems: list[str] = []
    seen: set[str] = set()

    with open(path, encoding="utf-8-sig", newline="") as handle:
        lines = (line for line in handle if line.strip() and not line.lstrip().startswith("#"))
        for number, row in enumerate(csv.DictReader(lines), start=1):
            try:
                domain = normalise_domain(row.get("domain") or "")
            except ValueError as exc:
                problems.append(f"row {number} ({row.get('domain')!r}): {exc}")
                continue
            if domain in seen:
                problems.append(f"row {number} ({domain}): duplicate, ignored")
                continue
            seen.add(domain)
            media = tuple(
                m.strip() for m in (row.get("media_types") or "image|video").split("|") if m.strip()
            )
            entries.append(
                DomainEntry(
                    domain=domain,
                    category=(row.get("category") or "").strip(),
                    media_types=media or ("image", "video"),
                    notes=(row.get("notes") or "").strip(),
                    last_checked=(row.get("last_checked") or "").strip(),
                )
            )
    return entries, problems