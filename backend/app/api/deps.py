"""Shared dependencies: who is logged in, and which case they are allowed to touch."""

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import Case, User, Scan

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    user_id = decode_access_token(token)
    user = db.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_owned_case(
    case_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Case:
    """Return the case only if it belongs to the logged-in user.

    Someone else's case gives the same 404 as a case that does not exist,
    so nobody can discover which case ids exist."""
    case = db.scalar(select(Case).where(Case.id == case_id, Case.owner_id == user.id))
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    return case

def get_owned_scan(
    scan_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Scan:
    """Return the scan only if its case belongs to the logged-in user (else a plain 404)."""
    scan = db.scalar(
        select(Scan)
        .join(Case, Case.id == Scan.case_id)
        .where(Scan.id == scan_id, Case.owner_id == user.id)
    )
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found.")
    return scan