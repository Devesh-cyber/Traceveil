import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.takedown import TakedownStatus


class TakedownOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    match_id: uuid.UUID | None
    domain: str
    contact: str | None
    contact_verified: bool
    contact_source: str | None
    subject: str
    body: str
    status: TakedownStatus
    sent_at: datetime | None
    sent_via: str | None
    response_note: str | None
    history: list
    created_at: datetime
    placeholders_left: list[str]
    warnings: list[str]


class TakedownCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    sender_name: str | None = Field(default=None, max_length=200)
    reply_to: str | None = Field(default=None, max_length=300)


class TakedownEdit(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    subject: str | None = Field(default=None, min_length=3, max_length=300)
    body: str | None = Field(default=None, min_length=20, max_length=20000)
    contact: str | None = Field(default=None, max_length=500)


class MarkSent(BaseModel):
    via: Literal["email", "web_form", "other"]
    sent_at: datetime | None = None  # when you really sent it; defaults to now


class StatusUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: Literal["acknowledged", "removed", "rejected", "no_response", "still_live", "sent"]
    note: str | None = Field(default=None, max_length=2000)
