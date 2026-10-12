import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin, IdMixin


class ScanStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class Scan(IdMixin, CreatedAtMixin, Base):
    """One search run for one reference file."""

    __tablename__ = "scans"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    evidence_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_files.id", ondelete="CASCADE")
    )
    status: Mapped[ScanStatus] = mapped_column(
        Enum(ScanStatus, native_enum=False, length=32), default=ScanStatus.queued, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    truncation_reason: Mapped[str | None] = mapped_column(String(200), default=None)
    # Live counters and the final coverage summary, e.g. {"checked": 40, "blocked": 12}
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)

    case: Mapped["Case"] = relationship(back_populates="scans")  # noqa: F821
    domain_results: Mapped[list["DomainResult"]] = relationship(  # noqa: F821
        back_populates="scan", cascade="all, delete-orphan", passive_deletes=True
    )
    matches: Mapped[list["Match"]] = relationship(  # noqa: F821
        back_populates="scan", cascade="all, delete-orphan", passive_deletes=True
    )