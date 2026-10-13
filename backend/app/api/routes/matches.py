from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_match
from app.core import storage
from app.core.audit import record
from app.db.session import get_db
from app.models import EvidenceFile, Match, ReviewStatus, User
from app.schemas.match import MatchOut, ReviewUpdate

router = APIRouter(prefix="/matches", tags=["matches"])

MIN_VERIFY_NOTE = 10


@router.get("/{match_id}", response_model=MatchOut)
def get_match(match: Match = Depends(get_owned_match)) -> Match:
    return match


@router.post("/{match_id}/review", response_model=MatchOut)
def review_match(
    payload: ReviewUpdate,
    match: Match = Depends(get_owned_match),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Match:
    """A person decides what a machine finding really is.

    potential  -> only found by the program
    reviewed   -> a person looked at it
    verified   -> a person confirmed it, with a preserved copy and a written note
    dismissed  -> a person decided it is a false alarm
    """
    old, new = match.review_status, payload.status

    if new == ReviewStatus.verified:
        if old != ReviewStatus.reviewed:
            raise HTTPException(409, "A match must be marked as reviewed before it can be verified.")
        if not match.has_snapshot:
            raise HTTPException(409, "A match can only be verified while a preserved copy of the matched image exists.")
        if len((payload.note or "").strip()) < MIN_VERIFY_NOTE:
            raise HTTPException(422, f"Verifying needs a note saying what you checked (at least {MIN_VERIFY_NOTE} characters).")

    match.review_status = new
    if new == ReviewStatus.potential:  # undoing a review clears who and when
        match.reviewed_at = None
        match.reviewed_by_id = None
        match.review_note = None
    else:
        match.reviewed_at = datetime.now(timezone.utc)
        match.reviewed_by_id = user.id
        if payload.note is not None:
            match.review_note = payload.note or None

    record(db, "match.reviewed", user_id=user.id, case_id=match.scan.case_id,
           match_id=str(match.id), from_status=old.value, to_status=new.value)
    db.commit()
    return match


@router.get("/{match_id}/snapshot")
def get_snapshot(
    match: Match = Depends(get_owned_match),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The private copy of the matched image. Every viewing is written to the audit trail."""
    evidence = db.get(EvidenceFile, match.snapshot_id) if match.snapshot_id else None
    if evidence is None:
        raise HTTPException(404, "No preserved copy exists for this match.")
    try:
        data = storage.read_bytes(evidence.storage_path)
    except (OSError, ValueError):
        raise HTTPException(404, "The preserved copy is no longer available.")
    record(db, "match.snapshot_viewed", user_id=user.id, case_id=match.scan.case_id,
           match_id=str(match.id), sha256=evidence.sha256)
    db.commit()
    return Response(
        content=data,
        media_type=evidence.content_type,  # one of three types we decided ourselves, never the website's header
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'inline; filename="matched-image"',
        },
    )