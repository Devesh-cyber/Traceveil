from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_case
from app.core.audit import record
from app.db.session import get_db
from app.models import Case, User
from app.reporting.evidence_report import build_case_report, render_markdown

router = APIRouter(tags=["reports"])


@router.get("/cases/{case_id}/report")
def case_report(
    case: Case = Depends(get_owned_case),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    format: Literal["json", "markdown"] = "json",
    include_potential: bool = Query(False, description="Also list findings no person has reviewed"),
):
    """The evidence report. By default only findings a person has reviewed are included."""
    report = build_case_report(db, case, include_potential=include_potential)
    record(db, "report.generated", user_id=user.id, case_id=case.id,
           format=format, include_potential=include_potential)
    db.commit()
    headers = {"Cache-Control": "no-store"}
    if format == "markdown":
        return PlainTextResponse(render_markdown(report), media_type="text/markdown; charset=utf-8", headers=headers)
    return JSONResponse(report, headers=headers)