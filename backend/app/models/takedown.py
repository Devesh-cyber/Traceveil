import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, IdMixin


class TakedownStatus(str, enum.Enum):
    draft = "draft"                  # written, nothing sent
    sent = "sent"                    # the USER says they sent it
    acknowledged = "acknowledged"    # the website replied
    removed = "removed"              # the website says (or the user saw) it is gone
    rejected = "rejected"            # the website refused
    no_response = "no_response"      # nothing came back
    still_live = "still_live"        # the user checked and the content is still online


class TakedownRequest(IdMixin, CreatedAtMixin, Base):
    """A takedown notice the user wrote and sent THEMSELVES. This program never sends one."""

    __tablename__ = "takedown_requests"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    match_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("matches.id", ondelete="SET NULL"), default=None
    )
    domain: Mapped[str] = mapped_column(String(255))
    contact: Mapped[str | None] = mapped_column(String(500), default=None)
    contact_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    contact_source: Mapped[str | None] = mapped_column(String(500), default=None)
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[TakedownStatus] = mapped_column(
        Enum(TakedownStatus, native_enum=False, length=32), default=TakedownStatus.draft
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    sent_via: Mapped[str | None] = mapped_column(String(32), default=None)  # email | web_form | other
    response_note: Mapped[str | None] = mapped_column(Text, default=None)
    # Every change of status: [{"at":..., "from":..., "to":..., "note":...}]
    history: Mapped[list] = mapped_column(JSONB, default=list)

    @property
    def placeholders_left(self) -> list[str]:
        """Parts of the draft the user still has to fill in, written like [YOUR NAME]."""
        import re
        return sorted(set(re.findall(r"\[[A-Z][A-Z ]+\]", f"{self.subject}\n{self.body}")))

    @property
    def warnings(self) -> list[str]:
        notes = []
        if self.contact is None:
            notes.append("No contact is known for this website. Find its abuse or content-removal page yourself, then enter it.")
        elif not self.contact_verified:
            notes.append("This contact has not been verified by the team. Check the website's own pages before using it.")
        if self.placeholders_left:
            notes.append("The draft still has parts to fill in: " + ", ".join(self.placeholders_left) + ".")
        return notes
