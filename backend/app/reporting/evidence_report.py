"""The case evidence report: what was searched, what was found, what a person decided.

Written for a victim, a lawyer or a platform's trust-and-safety team. It is deliberately
careful about what it claims, and it carries a digest so any later change to the report's
content can be detected.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog, Case, DomainResult, EvidenceFile, EvidenceKind, Match, ReviewStatus, Scan, TakedownRequest,
)

REPORT_VERSION = "1"

NOTICE = (
    "This report lists images that an automated visual comparison found similar to a "
    "reference file supplied by the case owner. A similarity result is not proof that "
    "content is non-consensual, authentic or unlawful, and it does not identify who "
    "published it. Findings marked 'verified' were confirmed by a person who recorded a "
    "note. Websites listed as blocked, unreachable or skipped were NOT searched, so an "
    "absence of findings is not evidence that no copies exist."
)

_SAFE_AUDIT_KEYS = ("from_status", "to_status", "scan_id", "match_id", "evidence_id", "sha256")


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def canonical_json(body: dict) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_of(body: dict) -> str:
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def verify_digest(report: dict) -> bool:
    """True if the report's content still matches the digest it carries."""
    body = {k: v for k, v in report.items() if k != "integrity"}
    return report.get("integrity", {}).get("digest") == digest_of(body)


def build_case_report(db: Session, case: Case, *, include_potential: bool = False) -> dict:
    references = list(db.scalars(
        select(EvidenceFile).where(EvidenceFile.case_id == case.id, EvidenceFile.kind.in_([EvidenceKind.reference_image, EvidenceKind.reference_video]))
        .order_by(EvidenceFile.created_at)
    ))
    scans = list(db.scalars(select(Scan).where(Scan.case_id == case.id).order_by(Scan.created_at)))

    shown = [ReviewStatus.reviewed, ReviewStatus.verified]
    if include_potential:
        shown.insert(0, ReviewStatus.potential)

    scan_entries, findings = [], []
    counts = {status.value: 0 for status in ReviewStatus}
    for scan in scans:
        sites = list(db.scalars(select(DomainResult).where(DomainResult.scan_id == scan.id).order_by(DomainResult.domain)))
        scan_entries.append({
            "id": str(scan.id),
            "status": scan.status.value,
            "started_at": _iso(scan.started_at),
            "finished_at": _iso(scan.finished_at),
            "error": scan.error,
            "truncated": scan.truncated,
            "truncation_reason": scan.truncation_reason,
            "stats": scan.stats or {},
            "sites": [
                {"domain": s.domain, "status": s.status.value, "pages_scanned": s.pages_scanned,
                 "images_compared": s.images_compared, "detail": s.detail}
                for s in sites
            ],
        })
        for match in db.scalars(select(Match).where(Match.scan_id == scan.id).order_by(Match.created_at)):
            counts[match.review_status.value] += 1
            if match.review_status not in shown:
                continue
            copy = db.get(EvidenceFile, match.snapshot_id) if match.snapshot_id else None
            findings.append({
                "id": str(match.id),
                "scan_id": str(scan.id),
                "domain": match.domain,
                "source_page_url": match.source_page_url,
                "image_url": match.image_url,
                "match_kind": match.kind.value,
                "signals": match.signals,
                "discovered_at": _iso(match.created_at),
                "review_status": match.review_status.value,
                "reviewed_at": _iso(match.reviewed_at),
                "review_note": match.review_note,
                "preserved_copy": None if copy is None else {
                    "sha256": copy.sha256, "size_bytes": copy.size_bytes,
                    "content_type": copy.content_type, "captured_at": _iso(copy.created_at),
                },
            })

    takedowns = [
        {
            "id": str(t.id), "domain": t.domain, "contact": t.contact,
            "contact_verified": t.contact_verified, "subject": t.subject,
            "status": t.status.value, "sent_at": _iso(t.sent_at), "sent_via": t.sent_via,
            "response_note": t.response_note, "history": t.history or [],
        }
        for t in db.scalars(select(TakedownRequest).where(TakedownRequest.case_id == case.id).order_by(TakedownRequest.created_at))
    ]

    audit = []
    for row in db.scalars(select(AuditLog).where(AuditLog.case_id == case.id).order_by(AuditLog.created_at).limit(1000)):
        audit.append({
            "at": _iso(row.created_at), "action": row.action,
            "detail": {k: v for k, v in (row.detail or {}).items() if k in _SAFE_AUDIT_KEYS},
        })

    body = {
        "report_version": REPORT_VERSION,
        "generated_at": _iso(datetime.now(timezone.utc)),
        "generated_by": "Traceveil",
        "notice": NOTICE,
        "includes_unreviewed_findings": include_potential,
        "case": {"id": str(case.id), "title": case.title, "description": case.description,
                 "created_at": _iso(case.created_at)},
        "reference_files": [
            {"id": str(r.id), "type": "video" if r.kind == EvidenceKind.reference_video else "image",
             "filename": r.original_filename, "sha256": r.sha256,
             "size_bytes": r.size_bytes, "uploaded_at": _iso(r.created_at)}
            for r in references
        ],
        "scans": scan_entries,
        "finding_counts": counts,
        "findings": findings,
        "takedown_requests": takedowns,
        "audit_trail": audit,
    }
    return {**body, "integrity": {
        "algorithm": "sha256",
        "digest": digest_of(body),
        "covers": "every other field of this report, as canonical JSON (sorted keys, no spaces)",
    }}


def render_markdown(report: dict) -> str:
    c = report["case"]
    lines = [
        f"# Evidence report: {c['title']}", "",
        f"Generated {report['generated_at']} (UTC) by {report['generated_by']}.", "",
        f"> {report['notice']}", "",
    ]
    if report["includes_unreviewed_findings"]:
        lines += ["**This version also lists findings that no person has reviewed yet.**", ""]
    if c["description"]:
        lines += ["## Case", c["description"], ""]

    lines += ["## Reference files", ""]
    for r in report["reference_files"] or []:
        lines.append(f"- {r['filename']} ({r['type']}): SHA-256 `{r['sha256']}`, {r['size_bytes']} bytes, uploaded {r['uploaded_at']}")
    if not report["reference_files"]:
        lines.append("- none")

    lines += ["", "## Searches and coverage", ""]
    for s in report["scans"]:
        cov = s["stats"].get("coverage", {})
        lines.append(f"### Scan {s['id']}")
        lines.append(f"- Status: {s['status']} (started {s['started_at']}, finished {s['finished_at']})")
        if cov:
            lines.append("- Websites: " + ", ".join(f"{n} {k}" for k, n in cov.items()))
        if s["truncated"]:
            lines.append(f"- Stopped early: {s['truncation_reason']}")
        if s["error"]:
            lines.append(f"- Error: {s['error']}")
        for site in s["sites"]:
            note = f" ({site['detail']})" if site["detail"] else ""
            lines.append(f"  - {site['domain']}: {site['status']}{note}")
        lines.append("")

    counts = report["finding_counts"]
    lines += ["## Findings", "", "All findings in the case: " + ", ".join(f"{n} {k}" for k, n in counts.items()), ""]
    if not report["findings"]:
        lines.append("No findings are included in this report.")
    for i, f in enumerate(report["findings"], 1):
        lines += [
            f"### {i}. {f['domain']} ({f['review_status'].upper()})",
            f"- Page: {f['source_page_url']}",
            f"- Image: {f['image_url']}",
            f"- Machine finding: {f['match_kind']} (measurements: {json.dumps(f['signals'], sort_keys=True)})",
            f"- Discovered: {f['discovered_at']}",
        ]
        if f["reviewed_at"]:
            lines.append(f"- Reviewed: {f['reviewed_at']}")
        if f["review_note"]:
            lines.append(f"- Reviewer's note: {f['review_note']}")
        copy = f["preserved_copy"]
        lines.append(
            f"- Preserved copy: SHA-256 `{copy['sha256']}`, captured {copy['captured_at']}" if copy
            else "- Preserved copy: none"
        )
        lines.append("")

    lines += ["## Takedown requests", ""]
    for t in report["takedown_requests"]:
        sent = f", sent {t['sent_at']} via {t['sent_via']}" if t["sent_at"] else ""
        lines.append(f"- {t['domain']}: {t['status']}{sent} (contact: {t['contact'] or 'none'}, "
                     f"{'verified' if t['contact_verified'] else 'not verified'})")
        for step in t["history"]:
            note = f": {step['note']}" if step.get("note") else ""
            lines.append(f"  - {step['at']} {step['from'] or 'start'} -> {step['to']}{note}")
    if not report["takedown_requests"]:
        lines.append("- none")
    lines.append("")
    lines += ["## Audit trail", ""]
    for a in report["audit_trail"]:
        extra = f" {a['detail']}" if a["detail"] else ""
        lines.append(f"- {a['at']} {a['action']}{extra}")
    lines += ["", "## Integrity", "",
              f"Digest of this report's data (SHA-256 of its canonical JSON): `{report['integrity']['digest']}`",
              "",
              "The JSON download carries its own digest. Downloads made at different times "
              "differ, because each records the time it was generated."]
    return "\n".join(lines) + "\n"