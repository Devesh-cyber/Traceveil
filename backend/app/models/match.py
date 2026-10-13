import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin, IdMixin


class MatchKind(str, enum.Enum):
    exact = "exact"
    near_duplicate = "near_duplicate"
    structural = "structural"
    probable = "probable"


class ReviewStatus(str, enum.Enum):
    potential = "potential"  # found by the machine only
    reviewed = "reviewed"    # a person looked at it
    verified = "verified"    # a person confirmed it with evidence
    dismissed = "dismissed"  # a person decided it is a false alarm


class Match(IdMixin, CreatedAtMixin, Base):
    """A page image that looks like the reference. A score is not proof."""

    __tablename__ = "matches"

    scan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), index=True
    )
    domain: Mapped[str] = mapped_column(String(255), index=True)
    source_page_url: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str] = mapped_column(Text)
    kind: Mapped[MatchKind] = mapped_column(Enum(MatchKind, native_enum=False, length=32))
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, native_enum=False, length=32), default=ReviewStatus.potential
    )
    # The signals behind the match: hash distances, inlier counts, variant used...
    signals: Mapped[dict] = mapped_column(JSONB, default=dict)
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    review_note: Mapped[str | None] = mapped_column(Text, default=None)
    # A private copy of the matched image, kept as evidence (an evidence_files row)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence_files.id", ondelete="SET NULL"), default=None
    )

    scan: Mapped["Scan"] = relationship(back_populates="matches")  # noqa: F821

    @property
    def has_snapshot(self) -> bool:
        return self.snapshot_id is not None