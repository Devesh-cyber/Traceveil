"""Helper for writing the audit trail."""

import uuid

from sqlalchemy.orm import Session

from app.models import AuditLog


def record(
    db: Session,
    action: str,
    *,
    user_id: uuid.UUID | None = None,
    case_id: uuid.UUID | None = None,
    **detail: object,
) -> None:
    """Add an audit row. The caller commits, so it lands in the same transaction
    as the action it describes."""
    db.add(AuditLog(action=action, user_id=user_id, case_id=case_id, detail=detail))