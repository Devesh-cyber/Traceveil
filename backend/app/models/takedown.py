import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, IdMixin


class TakedownStatus(str, enum.Enum):
    draft = "draft"
    sent = "sent"
    acknowledged = "acknowledged"
    removed = "removed"
    rejected = "rejected"
    no_response = "no_response"


class TakedownRequest(IdMixin, CreatedAtMixin, Base):
    """A takedown notice the user wrote and sent themselves. We never send automatically."""

    __tablename__ = "takedown_requests"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    match_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("matches.id", ondelete="SET NULL"), default=None
    )
    domain: Mapped[str] = mapped_column(String(255))
    contact: Mapped[str | None] = mapped_column(String(500), default=None)
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[TakedownStatus] = mapped_column(
        Enum(TakedownStatus, native_enum=False, length=32), default=TakedownStatus.draft
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    response_note: Mapped[str | None] = mapped_column(Text, default=None)