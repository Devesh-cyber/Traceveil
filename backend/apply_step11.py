"""Step 11: applies the small edits to files you already have.
Run from the backend folder:   python apply_step11.py
Safe to run twice. If a snippet is not found, it says which one and exits with an error."""

import sys
from pathlib import Path

ERRORS = []


def edit(path, pairs):
    text = Path(path).read_text()
    for old, new in pairs:
        if new in text:
            continue  # already applied
        if text.count(old) != 1:
            ERRORS.append(f"{path}: expected 1 match, found {text.count(old)} for {old[:60]!r}")
            continue
        text = text.replace(old, new)
    Path(path).write_text(text)


if not Path("app/main.py").exists():
    sys.exit("Run this from the backend folder (the one that contains app/).")

edit("app/main.py", [
    ("matches, reports, scans", "matches, reports, scans, takedowns"),
    ('    app.include_router(reports.router, prefix="/api")\n',
     '    app.include_router(reports.router, prefix="/api")\n    app.include_router(takedowns.router, prefix="/api")\n'),
])

edit("app/reporting/evidence_report.py", [
    ("    AuditLog, Case, DomainResult, EvidenceFile, EvidenceKind, Match, ReviewStatus, Scan,\n)",
     "    AuditLog, Case, DomainResult, EvidenceFile, EvidenceKind, Match, ReviewStatus, Scan, TakedownRequest,\n)"),
    ("    audit = []\n", '''    takedowns = [
        {
            "id": str(t.id), "domain": t.domain, "contact": t.contact,
            "contact_verified": t.contact_verified, "subject": t.subject,
            "status": t.status.value, "sent_at": _iso(t.sent_at), "sent_via": t.sent_via,
            "response_note": t.response_note, "history": t.history or [],
        }
        for t in db.scalars(select(TakedownRequest).where(TakedownRequest.case_id == case.id).order_by(TakedownRequest.created_at))
    ]

    audit = []
'''),
    ('        "findings": findings,\n        "audit_trail": audit,',
     '        "findings": findings,\n        "takedown_requests": takedowns,\n        "audit_trail": audit,'),
    ('    lines += ["## Audit trail", ""]', '''    lines += ["## Takedown requests", ""]
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
    lines += ["## Audit trail", ""]'''),
])

if ERRORS:
    print("SOME EDITS DID NOT APPLY:")
    for e in ERRORS:
        print(" -", e)
    sys.exit(1)
print("Step 11 edits applied.")
