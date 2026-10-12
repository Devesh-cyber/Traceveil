import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

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