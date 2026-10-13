"""Private storage for the files users give us (reference images).

Files live on disk under data_dir/evidence/<case id>/<random name>. The database keeps
only the relative path and a SHA-256 fingerprint of the file. Names on disk are random and
never taken from the upload, so a hostile file name can never choose where a file goes.
(Step 11 adds encryption at rest.)
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path

from app.core.config import get_settings


def evidence_root() -> Path:
    return get_settings().data_dir / "evidence"


def case_dir(case_id: uuid.UUID) -> Path:
    return evidence_root() / str(case_id)


def resolve(relative_path: str) -> Path:
    """Turn a stored relative path into a real path, refusing anything outside the root."""
    root = evidence_root().resolve()
    path = (root / relative_path).resolve()
    if root != path and root not in path.parents:
        raise ValueError("path escapes the evidence folder")
    return path


def save_bytes(case_id: uuid.UUID, data: bytes) -> tuple[str, str]:
    """Write a file privately. Returns (relative path, sha256 hex)."""
    folder = case_dir(case_id)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    relative = f"{case_id}/{uuid.uuid4().hex}.bin"
    path = resolve(relative)
    # O_EXCL: never overwrite; 0o600: readable only by the account running the server
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return relative, hashlib.sha256(data).hexdigest()


def incoming_path() -> Path:
    """A fresh private file path for an upload that is still being checked."""
    folder = evidence_root() / ".incoming"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder / f"{uuid.uuid4().hex}.part"


def adopt_file(case_id: uuid.UUID, source: Path) -> str:
    """Move a checked upload into its case folder under a random name. Returns the relative path."""
    folder = case_dir(case_id)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    relative = f"{case_id}/{uuid.uuid4().hex}.bin"
    os.replace(source, resolve(relative))
    os.chmod(resolve(relative), 0o600)
    return relative


def read_bytes(relative_path: str) -> bytes:
    return resolve(relative_path).read_bytes()


def delete_file(relative_path: str) -> None:
    try:
        resolve(relative_path).unlink(missing_ok=True)
    except ValueError:
        pass


def delete_case_files(case_id: uuid.UUID) -> None:
    shutil.rmtree(case_dir(case_id), ignore_errors=True)