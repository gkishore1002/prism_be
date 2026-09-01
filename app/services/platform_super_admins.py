"""Platform super admin account management."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.super_admin import SuperAdmin
from app.schemas import PlatformSuperAdminOut
import logging

logger = logging.getLogger(__name__)


def super_admin_out(row: SuperAdmin) -> PlatformSuperAdminOut:
    return PlatformSuperAdminOut(
        id=row.id,
        email=row.email,
        full_name=row.full_name,
        is_active=bool(row.is_active),
    )


def list_super_admins(public_db: Session) -> list[PlatformSuperAdminOut]:
    rows = public_db.query(SuperAdmin).order_by(SuperAdmin.full_name, SuperAdmin.email).all()
    return [super_admin_out(row) for row in rows]


def create_super_admin(
    public_db: Session,
    *,
    email: str,
    full_name: str,
    password: str,
) -> PlatformSuperAdminOut:
    normalized_email = email.strip().lower()
    existing = public_db.query(SuperAdmin).filter(SuperAdmin.email == normalized_email).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Super admin with this email already exists")

    row = SuperAdmin(
        id=f"sa-{uuid.uuid4().hex[:8]}",
        email=normalized_email,
        full_name=full_name.strip(),
        password_hash=hash_password(password),
        is_active=True,
    )
    public_db.add(row)
    public_db.commit()
    public_db.refresh(row)
    return super_admin_out(row)


def ensure_platform_super_admin_if_missing(public_db: Session) -> SuperAdmin | None:
    """Create the first SYSTEM superuser from env. No demo org. No-op if one exists."""
    from app.core.config import settings

    email = (settings.platform_super_admin_email or "").strip().lower()
    password = (settings.platform_super_admin_password or "").strip()
    name = (settings.platform_super_admin_name or "Platform Super Admin").strip()

    if not email or not password:
        return None
    if len(password) < 8:
        logger.warning("PLATFORM_SUPER_ADMIN_PASSWORD is set but shorter than 8 characters — skipped")
        return None
    if public_db.query(SuperAdmin).first():
        return None

    row = SuperAdmin(
        id=f"sa-{uuid.uuid4().hex[:8]}",
        email=email,
        full_name=name,
        password_hash=hash_password(password),
        is_active=True,
    )
    public_db.add(row)
    public_db.commit()
    public_db.refresh(row)
    logger.info("Created platform superuser %s — sign in with organization SYSTEM", email)
    return row
