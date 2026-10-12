import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.domain_result import DomainStatus
from app.models.scan import ScanStatus


class ScanCreate(BaseModel):
    evidence_id: uuid.UUID


class ScanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    evidence_file_id: uuid.UUID
    status: ScanStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    truncated: bool
    truncation_reason: str | None
    stats: dict


class DomainResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    domain: str
    status: DomainStatus
    pages_scanned: int
    images_compared: int
    detail: str | None
    checked_at: datetime | None