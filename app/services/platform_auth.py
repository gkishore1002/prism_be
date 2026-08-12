"""Platform super user constants and helpers (Swotify-style SYSTEM login)."""

from __future__ import annotations

SYSTEM_ORG_CODE = "SYSTEM"
SYSTEM_INSTITUTION_ID = "00000000-0000-0000-0000-000000000000"
SUPER_USER_ROLE = "super_user"


def is_system_org_code(code: str | None) -> bool:
    return bool(code and code.strip().upper() == SYSTEM_ORG_CODE)


def is_super_user_token(payload: dict) -> bool:
    return payload.get("role") == SUPER_USER_ROLE
