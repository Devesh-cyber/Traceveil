import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.match import MatchKind, ReviewStatus


class MatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scan_id: uuid.UUID
    domain: str
    source_page_url: str
    image_url: str
    kind: MatchKind
    review_status: ReviewStatus
    signals: dict
    created_at: datetime
    reviewed_at: datetime | None
    review_note: str | None
    has_snapshot: bool


class ReviewUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: ReviewStatus
    note: str | None = Field(default=None, max_length=2000)