import hashlib
import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_case
from app.core import storage
from app.core.audit import record
from app.core.config import get_settings
from app.db.session import get_db
from app.matching.fingerprint import ImageError, load_image
from app.models import Case, EvidenceFile, EvidenceKind, Scan, User
from app.schemas.evidence import EvidenceOut
from app.video.keyframes import VideoError, extract_keyframes, probe

router = APIRouter(prefix="/cases/{case_id}/evidence", tags=["evidence"])

CONTENT_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def _read_limited(upload: UploadFile, limit: int) -> bytes:
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "File is too large.")
    return data


def _safe_name(name: str | None) -> str:
    base = PurePosixPath((name or "upload").replace("\\", "/")).name
    return base[:255] or "upload"


@router.post("", response_model=EvidenceOut, status_code=status.HTTP_201_CREATED)
def upload_reference_image(
    file: UploadFile,
    case: Case = Depends(get_owned_case),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EvidenceFile:
    settings = get_settings()
    data = _read_limited(file, settings.max_upload_bytes)
    try:
        load_image(data)  # proves it is a real, safe, supported image
    except ImageError as exc:
        raise HTTPException(422, str(exc)) from exc
    content_type = CONTENT_TYPES[Image.open(io.BytesIO(data)).format]

    relative_path, digest = storage.save_bytes(case.id, data)
    try:
        evidence = EvidenceFile(
            case_id=case.id,
            kind=EvidenceKind.reference_image,
            original_filename=_safe_name(file.filename),
            content_type=content_type,
            size_bytes=len(data),
            sha256=digest,
            storage_path=relative_path,
            delete_after=datetime.now(timezone.utc) + timedelta(days=settings.retention_days),
        )
        db.add(evidence)
        db.flush()
        record(db, "evidence.uploaded", user_id=user.id, case_id=case.id,
               evidence_id=str(evidence.id), sha256=digest)
        db.commit()
    except Exception:
        db.rollback()
        storage.delete_file(relative_path)  # never leave an orphan file behind
        raise
    return evidence


@router.post("/video", response_model=EvidenceOut, status_code=status.HTTP_201_CREATED)
def upload_reference_video(
    file: UploadFile,
    case: Case = Depends(get_owned_case),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EvidenceFile:
    """Accept a video the user is authorised to use as the reference for a scan.

    It is streamed to a private temporary file (never held in memory), cut off at the size
    limit, and only moved into the case folder after we have proved that it is a short video
    with usable frames. Whatever goes wrong, the temporary file is removed."""
    settings = get_settings()
    temp = storage.incoming_path()
    digest, size = hashlib.sha256(), 0
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_video_bytes:
                    raise HTTPException(413, "File is too large.")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(422, "The file is empty.")
        try:
            info = probe(temp, settings.max_video_seconds)
            extract_keyframes(temp, info)  # proves there are real, usable frames
        except VideoError as exc:
            raise HTTPException(422, str(exc)) from exc
        relative_path = storage.adopt_file(case.id, temp)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise

    declared = (file.content_type or "").lower()
    try:
        evidence = EvidenceFile(
            case_id=case.id,
            kind=EvidenceKind.reference_video,
            original_filename=_safe_name(file.filename),
            content_type=declared[:100] if declared.startswith("video/") else "video/unknown",
            size_bytes=size,
            sha256=digest.hexdigest(),
            storage_path=relative_path,
            delete_after=datetime.now(timezone.utc) + timedelta(days=settings.retention_days),
        )
        db.add(evidence)
        db.flush()
        record(db, "evidence.uploaded", user_id=user.id, case_id=case.id,
               evidence_id=str(evidence.id), sha256=evidence.sha256, **info.as_dict())
        db.commit()
    except Exception:
        db.rollback()
        storage.delete_file(relative_path)
        raise
    return evidence


@router.get("", response_model=list[EvidenceOut])
def list_evidence(
    case: Case = Depends(get_owned_case), db: Session = Depends(get_db)
) -> list[EvidenceFile]:
    query = select(EvidenceFile).where(EvidenceFile.case_id == case.id).order_by(EvidenceFile.created_at)
    return list(db.scalars(query))


@router.delete("/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_evidence(
    evidence_id: str,
    case: Case = Depends(get_owned_case),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    evidence = db.scalar(
        select(EvidenceFile).where(EvidenceFile.id == evidence_id, EvidenceFile.case_id == case.id)
    ) if _is_uuid(evidence_id) else None
    if evidence is None:
        raise HTTPException(404, "File not found.")
    if db.scalar(select(Scan.id).where(Scan.evidence_file_id == evidence.id).limit(1)):
        raise HTTPException(
            409, "This file was used in scans. Delete the case to remove everything.",
        )
    path = evidence.storage_path
    record(db, "evidence.deleted", user_id=user.id, case_id=case.id, evidence_id=evidence_id)
    db.delete(evidence)
    db.commit()
    storage.delete_file(path)


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False