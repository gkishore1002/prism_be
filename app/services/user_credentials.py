"""Phone-based login credentials — email as {digits}@gmail.com, password defaults to phone."""

from __future__ import annotations

import re

from fastapi import HTTPException, status

PHONE_EMAIL_DOMAIN = "gmail.com"
MIN_PHONE_DIGITS = 10
MAX_PHONE_DIGITS = 15
MIN_PASSWORD_LENGTH = 8


def normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def phone_to_login_email(phone: str) -> str:
    digits = normalize_phone(phone)
    if len(digits) < MIN_PHONE_DIGITS or len(digits) > MAX_PHONE_DIGITS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phone number must be {MIN_PHONE_DIGITS}–{MAX_PHONE_DIGITS} digits",
        )
    return f"{digits}@{PHONE_EMAIL_DOMAIN}"


def resolve_user_credentials(*, phone: str, password: str | None = None) -> tuple[str, str]:
    """Return (login_email, password_hash_source) from phone and optional password override."""
    digits = normalize_phone(phone)
    if len(digits) < MIN_PHONE_DIGITS or len(digits) > MAX_PHONE_DIGITS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phone number must be {MIN_PHONE_DIGITS}–{MAX_PHONE_DIGITS} digits",
        )
    email = f"{digits}@{PHONE_EMAIL_DOMAIN}"
    resolved_password = (password or "").strip() or digits
    if len(resolved_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
        )
    return email, resolved_password
