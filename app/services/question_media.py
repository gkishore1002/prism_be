"""Persist and serve question stem/option images (photos as-is; no OCR)."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

_KEY_RE = re.compile(r"^[a-zA-Z0-9_-]+/[a-f0-9]{16,32}\.(jpg|png|webp)$")


def media_root() -> Path:
    root = Path(settings.question_media_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def media_url_for_key(key: str | None) -> str | None:
    if not key:
        return None
    return f"/question-media/{key}"


def assert_key_owned(key: str, institution_id: str) -> None:
    if not _KEY_RE.match(key):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid media key")
    owner, _, _rest = key.partition("/")
    if owner != institution_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Media access denied")


def resolve_media_path(key: str) -> Path:
    assert_key_owned(key, key.split("/", 1)[0])
    path = (media_root() / key).resolve()
    root = media_root().resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
    return path


async def save_upload(institution_id: str, file: UploadFile) -> str:
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    ext = ALLOWED_CONTENT_TYPES.get(content_type)
    if not ext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPEG, PNG, or WebP images are allowed",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty image file")
    if len(data) > settings.question_media_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Image must be under {settings.question_media_max_bytes // (1024 * 1024)} MB",
        )

    key = f"{institution_id}/{uuid.uuid4().hex}{ext}"
    dest = media_root() / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return key


def save_bytes(institution_id: str, data: bytes, *, ext: str = ".jpg") -> str:
    if ext not in {".jpg", ".png", ".webp"}:
        ext = ".jpg"
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty image file")
    if len(data) > settings.question_media_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Image must be under {settings.question_media_max_bytes // (1024 * 1024)} MB",
        )
    key = f"{institution_id}/{uuid.uuid4().hex}{ext}"
    dest = media_root() / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return key
