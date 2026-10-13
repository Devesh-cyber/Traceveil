"""Takedown requests: drafts the user edits, sends THEMSELVES, and then tracks here.

Nothing in this file sends anything anywhere. 'Marking as sent' only records what the user
says they did. Nothing is reported to the police or to anyone else either; the reporting
channels listed here are for the user to read and decide about.
"""

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_case, get_owned_match
from app.core.audit import record
from app.core.config import ROOT_DIR
from app.db.session import get_db
from app.models import Case, EvidenceFile, Match, ReviewStatus, TakedownRequest, TakedownStatus, User
from app.schemas.takedown import MarkSent, StatusUpdate, TakedownCreate, TakedownEdit, TakedownOut
from app.takedown.contacts import find_contact
from app.takedown.notice import build_notice

router = APIRouter(tags=["takedowns"])

S = TakedownStatus
# What the user may record next. A takedown that did not work can be sent again (a follow-up).
ALLOWED = {
    S.draft: {S.sent},
    S.sent: {S.acknowledged, S.removed, S.rejected, S.no_response, S.still_live},
    S.acknowledged: {S.removed, S.rejected, S.no_response, S.still_live},
    S.no_response: {S.sent, S.removed, S.rejected, S.still_live},
    S.still_live: {S.sent, S.removed, S.rejected, S.no_response},
    S.rejected: {S.sent, S.still_live},
    S.removed: {S.still_live},  # it came back
}
CHANNELS_FILE = ROOT_DIR / "backend" / "data" / "reporting_channels.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_owned_takedown(
    takedown_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TakedownRequest:
    item = db.scalar(
        select(TakedownRequest)
        .join(Case, Case.id == TakedownRequest.case_id)
        .where(TakedownRequest.id == takedown_id, Case.owner_id == user.id)
    )
    if item is None:
        raise HTTPException(404, "Takedown request not found.")
    return item


def _log(item: TakedownRequest, old: S | None, new: S, note: str | None) -> None:
    item.history = [*(item.history or []), {
        "at": _now().isoformat(), "from": old.value if old else None, "to": new.value, "note": note or None,
    }]


@router.post("/matches/{match_id}/takedown", response_model=TakedownOut, status_code=status.HTTP_201_CREATED)
def create_draft(
    payload: TakedownCreate,
    match: Match = Depends(get_owned_match),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TakedownRequest:
    """Write a draft for a match a person has reviewed. Nothing is sent."""
    if match.review_status not in (ReviewStatus.reviewed, ReviewStatus.verified):
        raise HTTPException(409, "Review the match first. A takedown request is only drafted for a match a person has looked at.")
    if db.scalar(select(TakedownRequest.id).where(
        TakedownRequest.match_id == match.id, TakedownRequest.status == S.draft
    ).limit(1)):
        raise HTTPException(409, "There is already an unsent draft for this match. Edit or delete it.")

    snapshot = db.get(EvidenceFile, match.snapshot_id) if match.snapshot_id else None
    subject, body = build_notice(
        match, snapshot_sha256=snapshot.sha256 if snapshot else None,
        sender_name=payload.sender_name, reply_to=payload.reply_to,
    )
    contact = find_contact(match.domain)
    case_id = match.scan.case_id
    item = TakedownRequest(
        case_id=case_id, match_id=match.id, domain=match.domain,
        contact=contact.address if contact else None,
        contact_verified=bool(contact and contact.verified),
        contact_source=(contact.source_url or None) if contact else None,
        subject=subject, body=body, status=S.draft, history=[],
    )
    _log(item, None, S.draft, "draft written")
    db.add(item)
    db.flush()
    record(db, "takedown.drafted", user_id=user.id, case_id=case_id, match_id=str(match.id), takedown_id=str(item.id))
    db.commit()
    return item


@router.get("/cases/{case_id}/takedowns", response_model=list[TakedownOut])
def list_takedowns(case: Case = Depends(get_owned_case), db: Session = Depends(get_db)) -> list[TakedownRequest]:
    return list(db.scalars(
        select(TakedownRequest).where(TakedownRequest.case_id == case.id).order_by(TakedownRequest.created_at)
    ))


@router.get("/takedowns/{takedown_id}", response_model=TakedownOut)
def get_takedown(item: TakedownRequest = Depends(get_owned_takedown)) -> TakedownRequest:
    return item


@router.patch("/takedowns/{takedown_id}", response_model=TakedownOut)
def edit_draft(
    payload: TakedownEdit,
    item: TakedownRequest = Depends(get_owned_takedown),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TakedownRequest:
    if item.status != S.draft:
        raise HTTPException(409, "Only an unsent draft can be edited.")
    changes = payload.model_dump(exclude_unset=True)
    if "subject" in changes and changes["subject"]:
        item.subject = changes["subject"]
    if "body" in changes and changes["body"]:
        item.body = changes["body"]
    if "contact" in changes:
        # A contact typed in by hand is, by definition, not one the team verified.
        item.contact = changes["contact"] or None
        item.contact_verified = False
        item.contact_source = None
    record(db, "takedown.edited", user_id=user.id, case_id=item.case_id, takedown_id=str(item.id))
    db.commit()
    return item


@router.delete("/takedowns/{takedown_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(
    item: TakedownRequest = Depends(get_owned_takedown),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    if item.status != S.draft:
        raise HTTPException(409, "Only an unsent draft can be deleted. A request that was sent stays in the history.")
    record(db, "takedown.deleted", user_id=user.id, case_id=item.case_id, takedown_id=str(item.id))
    db.delete(item)
    db.commit()


@router.post("/takedowns/{takedown_id}/sent", response_model=TakedownOut)
def mark_sent(
    payload: MarkSent,
    item: TakedownRequest = Depends(get_owned_takedown),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TakedownRequest:
    """You tell us you sent it (by email, a web form...). We do not, and cannot, send it."""
    if item.status != S.draft:
        raise HTTPException(409, "This request was already marked as sent. Use the status update for follow-ups.")
    if item.placeholders_left:
        raise HTTPException(409, "Fill in " + ", ".join(item.placeholders_left) + " before marking this as sent.")
    when = payload.sent_at or _now()
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    if when > _now():
        raise HTTPException(422, "The sent date cannot be in the future.")
    old = item.status
    item.status, item.sent_at, item.sent_via = S.sent, when, payload.via
    _log(item, old, S.sent, f"sent by the user via {payload.via}")
    record(db, "takedown.marked_sent", user_id=user.id, case_id=item.case_id, takedown_id=str(item.id), via=payload.via)
    db.commit()
    return item


@router.post("/takedowns/{takedown_id}/status", response_model=TakedownOut)
def update_status(
    payload: StatusUpdate,
    item: TakedownRequest = Depends(get_owned_takedown),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TakedownRequest:
    """Record what happened next: a reply, a refusal, silence, the content still being online."""
    new = S(payload.status)
    if new not in ALLOWED.get(item.status, set()):
        raise HTTPException(409, f"A request that is '{item.status.value}' cannot be changed to '{new.value}'.")
    old = item.status
    item.status = new
    if new == S.sent:  # a follow-up or re-submission
        item.sent_at = _now()
    if payload.note:
        item.response_note = payload.note
    _log(item, old, new, payload.note)
    record(db, "takedown.status", user_id=user.id, case_id=item.case_id, takedown_id=str(item.id),
           from_status=old.value, to_status=new.value)
    db.commit()
    return item


@router.get("/reporting-channels")
def reporting_channels(user: User = Depends(get_current_user)) -> dict:
    """Official places a person may choose to report to. Information only: we file nothing."""
    return json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
