from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_case
from app.core import storage
from app.core.audit import record
from app.db.session import get_db
from app.models import Case, User
from app.schemas.case import CaseCreate, CaseOut

router = APIRouter(prefix="/cases", tags=["cases"])


@router.post("", response_model=CaseOut, status_code=status.HTTP_201_CREATED)
def create_case(
    payload: CaseCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Case:
    case = Case(owner_id=user.id, title=payload.title, description=payload.description)
    db.add(case)
    db.flush()
    record(db, "case.created", user_id=user.id, case_id=case.id)
    db.commit()
    return case


@router.get("", response_model=list[CaseOut])
def list_cases(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[Case]:
    query = select(Case).where(Case.owner_id == user.id).order_by(Case.created_at.desc())
    return list(db.scalars(query))


@router.get("/{case_id}", response_model=CaseOut)
def get_case(case: Case = Depends(get_owned_case)) -> Case:
    return case


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_case(
    case: Case = Depends(get_owned_case),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    # The audit row keeps the id in its details, because its case link is cleared on delete.
    record(db, "case.deleted", user_id=user.id, deleted_case_id=str(case.id))
    case_id = case.id
    db.delete(case)  # the database also removes the case's scans, matches and files rows
    db.commit()
    storage.delete_case_files(case_id)  # and the files themselves leave the disk