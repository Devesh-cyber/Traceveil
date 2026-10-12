import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, IdMixin


class AuditLog(IdMixin, CreatedAtMixin, Base):
    """Append-only record of who did what. Rows are added, never edited."""

    __tablename__ = "audit_log"

    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="SET NULL"), index=True, default=None
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)