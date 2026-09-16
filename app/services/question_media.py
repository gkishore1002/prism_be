"""Persist and serve question stem/option images (photos as-is; no OCR)."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.academic import QuestionMediaFile

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

# Keys are "{institution_id}/{file}". Allow hyphenated ids and common image suffixes.
_KEY_RE = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}/[a-zA-Z0-9._-]+\.(jpe?g|png|webp)$",
    re.IGNORECASE,
)


def media_root() -> Path:
    root = Path(settings.question_media_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def media_url_for_key(key: str | None) -> str | None:
    if not key:
        return None
    return f"/question-media/{key}"


def _content_type_for_key(key: str) -> str:
    suffix = Path(key).suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "image/jpeg"


def assert_key_owned(key: str, institution_id: str) -> None:
    if ".." in key or not _KEY_RE.match(key):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid media key")
    owner, _, _rest = key.partition("/")
    if owner != institution_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Media access denied")


def _try_write_disk(key: str, data: bytes) -> None:
    try:
        dest = media_root() / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    except OSError:
        logger.warning("Could not write question media %s to disk", key, exc_info=True)


def _read_disk(key: str) -> bytes | None:
    try:
        path = (media_root() / key).resolve()
        root = media_root().resolve()
        if not str(path).startswith(str(root)) or not path.is_file():
            return None
        data = path.read_bytes()
        return data or None
    except OSError:
        logger.warning("Could not read question media %s from disk", key, exc_info=True)
        return None


def _upsert_db(
    db: Session,
    *,
    key: str,
    institution_id: str,
    data: bytes,
    content_type: str,
) -> None:
    row = db.get(QuestionMediaFile, key)
    if row:
        row.data = data
        row.content_type = content_type
        row.institution_id = institution_id
        return
    db.add(
        QuestionMediaFile(
            key=key,
            institution_id=institution_id,
            content_type=content_type,
            data=data,
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    )


def resolve_media_path(key: str) -> Path:
    """Legacy disk lookup — prefer load_media() so DB-backed files still serve."""
    assert_key_owned(key, key.split("/", 1)[0])
    data_path = media_root() / key
    path = data_path.resolve()
    root = media_root().resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
    return path


def load_media(db: Session, key: str, institution_id: str) -> tuple[bytes, str]:
    assert_key_owned(key, institution_id)
    row = db.get(QuestionMediaFile, key)
    if row and row.data:
        return bytes(row.data), row.content_type or _content_type_for_key(key)

    data = _read_disk(key)
    if data:
        content_type = _content_type_for_key(key)
        _upsert_db(db, key=key, institution_id=institution_id, data=data, content_type=content_type)
        db.commit()
        return data, content_type

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")


def _store(db: Session, institution_id: str, data: bytes, *, ext: str, content_type: str) -> str:
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty image file")
    if len(data) > settings.question_media_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Image must be under {settings.question_media_max_bytes // (1024 * 1024)} MB",
        )
    if ext not in {".jpg", ".png", ".webp"}:
        ext = ".jpg"
        content_type = "image/jpeg"
    key = f"{institution_id}/{uuid.uuid4().hex}{ext}"
    _try_write_disk(key, data)
    _upsert_db(db, key=key, institution_id=institution_id, data=data, content_type=content_type)
    return key


async def save_upload(db: Session, institution_id: str, file: UploadFile) -> str:
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    ext = ALLOWED_CONTENT_TYPES.get(content_type)
    if not ext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPEG, PNG, or WebP images are allowed",
        )
    data = await file.read()
    return _store(db, institution_id, data, ext=ext, content_type=content_type or "image/jpeg")


def save_bytes(db: Session, institution_id: str, data: bytes, *, ext: str = ".jpg") -> str:
    content_type = {".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")
    return _store(db, institution_id, data, ext=ext, content_type=content_type)
