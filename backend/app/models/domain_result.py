import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin


class DomainStatus(str, enum.Enum):
    checked = "checked"          # pages were read and images compared
    no_images = "no_images"      # site reachable but nothing to compare
    blocked = "blocked"          # refused us (403, captcha, our own safety guard)
    unreachable = "unreachable"  # timeout, DNS or connection failure
    skipped = "skipped"          # not reached because a limit was hit


class DomainResult(IdMixin, Base):
    """What happened for one website during one scan. 'No match' is not 'not checked'."""

    __tablename__ = "domain_results"

    scan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), index=True
    )
    domain: Mapped[str] = mapped_column(String(255))
    status: Mapped[DomainStatus] = mapped_column(Enum(DomainStatus, native_enum=False, length=32))
    pages_scanned: Mapped[int] = mapped_column(Integer, default=0)
    images_compared: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str | None] = mapped_column(Text, default=None)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    scan: Mapped["Scan"] = relationship(back_populates="domain_results")  # noqa: F821