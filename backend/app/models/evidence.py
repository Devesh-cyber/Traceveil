import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin, IdMixin


class EvidenceKind(str, enum.Enum):
    reference_image = "reference_image"
    reference_video = "reference_video"
    supporting = "supporting"


class EvidenceFile(IdMixin, CreatedAtMixin, Base):
    """A file the user gave us. The file itself lives on disk, not in the database."""

    __tablename__ = "evidence_files"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[EvidenceKind] = mapped_column(Enum(EvidenceKind, native_enum=False, length=32))
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_path: Mapped[str] = mapped_column(String(500))
    delete_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    case: Mapped["Case"] = relationship(back_populates="evidence_files")  # noqa: F821