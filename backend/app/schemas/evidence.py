import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.evidence import EvidenceKind


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    kind: EvidenceKind
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    delete_after: datetime | None