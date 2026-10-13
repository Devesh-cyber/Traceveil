"""Where a removal request for a website should go.

The list lives in data/contacts.csv and is filled in by people who looked at each website's
own pages. A contact only counts as VERIFIED when we also know which page showed it and when
somebody checked. Anything else is shown as unverified, and a website we know nothing about
gets no contact at all: we never guess an address like abuse@<domain>.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.core.config import ROOT_DIR
from app.crawler.domains import normalise_domain

DEFAULT_PATH = ROOT_DIR / "backend" / "data" / "contacts.csv"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class Contact:
    domain: str
    abuse_email: str = ""
    report_url: str = ""
    source_url: str = ""
    verified_on: str = ""
    notes: str = ""

    @property
    def address(self) -> str | None:
        """What goes in the draft's 'to' field: an email address, else a web form."""
        return self.abuse_email or self.report_url or None

    @property
    def verified(self) -> bool:
        return bool(self.source_url and self.verified_on and self.address)


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def load_contacts(path: Path = DEFAULT_PATH) -> tuple[dict[str, Contact], list[str]]:
    contacts: dict[str, Contact] = {}
    problems: list[str] = []
    if not path.exists():
        return contacts, [f"{path.name} not found"]
    with open(path, encoding="utf-8-sig", newline="") as handle:
        lines = (line for line in handle if line.strip() and not line.lstrip().startswith("#"))
        for number, row in enumerate(csv.DictReader(lines), start=1):
            try:
                domain = normalise_domain(row.get("domain") or "")
            except ValueError as exc:
                problems.append(f"row {number} ({row.get('domain')!r}): {exc}")
                continue
            email = (row.get("abuse_email") or "").strip()
            form = (row.get("report_url") or "").strip()
            source = (row.get("source_url") or "").strip()
            verified_on = (row.get("verified_on") or "").strip()
            if email and not _EMAIL.match(email):
                problems.append(f"row {number} ({domain}): abuse_email is not an email address, ignored")
                email = ""
            if form and not form.startswith("https://"):
                problems.append(f"row {number} ({domain}): report_url must start with https://, ignored")
                form = ""
            if source and not source.startswith("https://"):
                problems.append(f"row {number} ({domain}): source_url must start with https://, ignored")
                source = ""
            if verified_on and not _valid_date(verified_on):
                problems.append(f"row {number} ({domain}): verified_on must look like 2026-10-13, ignored")
                verified_on = ""
            if not (email or form):
                problems.append(f"row {number} ({domain}): no abuse_email or report_url, ignored")
                continue
            if domain in contacts:
                problems.append(f"row {number} ({domain}): duplicate, ignored")
                continue
            contacts[domain] = Contact(domain, email, form, source, verified_on, (row.get("notes") or "").strip())
    return contacts, problems


def find_contact(domain: str, contacts: dict[str, Contact] | None = None) -> Contact | None:
    """Exact match first, then the parent names (video.example.org -> example.org)."""
    if contacts is None:
        contacts, _ = load_contacts()
    labels = domain.lower().strip(".").split(".")
    for start in range(len(labels) - 1):
        found = contacts.get(".".join(labels[start:]))
        if found:
            return found
    return None
