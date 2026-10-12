from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_case, get_owned_scan
from app.core.audit import record
from app.db.session import get_db
from app.models import Case, DomainResult, EvidenceFile, EvidenceKind, Match, Scan, ScanStatus, User
from app.schemas.match import MatchOut
from app.schemas.scan import DomainResultOut, ScanCreate, ScanOut

router = APIRouter(tags=["scans"])

ACTIVE = (ScanStatus.queued, ScanStatus.running)


@router.post("/cases/{case_id}/scans", response_model=ScanOut, status_code=status.HTTP_201_CREATED)
def start_scan(
    payload: ScanCreate,
    case: Case = Depends(get_owned_case),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Scan:
    evidence = db.scalar(
        select(EvidenceFile).where(
            EvidenceFile.id == payload.evidence_id,
            EvidenceFile.case_id == case.id,
            EvidenceFile.kind == EvidenceKind.reference_image,
        )
    )
    if evidence is None:
        raise HTTPException(404, "Reference file not found in this case.")
    if db.scalar(select(Scan.id).where(Scan.case_id == case.id, Scan.status.in_(ACTIVE)).limit(1)):
        raise HTTPException(409, "A scan is already queued or running for this case.")

    scan = Scan(case_id=case.id, evidence_file_id=evidence.id, status=ScanStatus.queued, stats={})
    db.add(scan)
    db.flush()
    record(db, "scan.queued", user_id=user.id, case_id=case.id, scan_id=str(scan.id))
    db.commit()
    return scan


@router.get("/cases/{case_id}/scans", response_model=list[ScanOut])
def list_scans(case: Case = Depends(get_owned_case), db: Session = Depends(get_db)) -> list[Scan]:
    return list(db.scalars(select(Scan).where(Scan.case_id == case.id).order_by(Scan.created_at.desc())))


@router.get("/scans/{scan_id}", response_model=ScanOut)
def get_scan(scan: Scan = Depends(get_owned_scan)) -> Scan:
    """Poll this. `status` and `stats` reflect what the worker is really doing."""
    return scan


@router.get("/scans/{scan_id}/domains", response_model=list[DomainResultOut])
def scan_domains(scan: Scan = Depends(get_owned_scan), db: Session = Depends(get_db)) -> list[DomainResult]:
    """What happened to every website: checked, blocked, unreachable, ..."""
    return list(db.scalars(select(DomainResult).where(DomainResult.scan_id == scan.id).order_by(DomainResult.domain)))


@router.get("/scans/{scan_id}/matches", response_model=list[MatchOut])
def scan_matches(scan: Scan = Depends(get_owned_scan), db: Session = Depends(get_db)) -> list[Match]:
    return list(db.scalars(select(Match).where(Match.scan_id == scan.id).order_by(Match.created_at)))


@router.post("/scans/{scan_id}/cancel", response_model=ScanOut)
def cancel_scan(
    scan: Scan = Depends(get_owned_scan),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Scan:
    if scan.status not in ACTIVE:
        raise HTTPException(409, "Only a queued or running scan can be cancelled.")
    scan.status = ScanStatus.cancelled
    scan.finished_at = datetime.now(timezone.utc)
    record(db, "scan.cancelled", user_id=user.id, case_id=scan.case_id, scan_id=str(scan.id))
    db.commit()
    return scan