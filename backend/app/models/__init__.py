"""Import every model here so Alembic and Base.metadata can see all tables."""

from app.models.audit import AuditLog
from app.models.case import Case
from app.models.domain_result import DomainResult, DomainStatus
from app.models.evidence import EvidenceFile, EvidenceKind
from app.models.match import Match, MatchKind, ReviewStatus
from app.models.scan import Scan, ScanStatus
from app.models.takedown import TakedownRequest, TakedownStatus
from app.models.user import User

__all__ = [
    "AuditLog", "Case", "DomainResult", "DomainStatus", "EvidenceFile",
    "EvidenceKind", "Match", "MatchKind", "ReviewStatus", "Scan",
    "ScanStatus", "TakedownRequest", "TakedownStatus", "User",
]