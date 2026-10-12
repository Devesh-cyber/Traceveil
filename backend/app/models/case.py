import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin, IdMixin


class Case(IdMixin, CreatedAtMixin, Base):
    """A private investigation. Everything else hangs off a case."""

    __tablename__ = "cases"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, default=None)

    owner: Mapped["User"] = relationship(back_populates="cases")  # noqa: F821
    evidence_files: Mapped[list["EvidenceFile"]] = relationship(  # noqa: F821
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True
    )
    scans: Mapped[list["Scan"]] = relationship(  # noqa: F821
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True
    )