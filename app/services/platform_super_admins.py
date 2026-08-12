"""Platform super admin account management."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.super_admin import SuperAdmin
from app.schemas import PlatformSuperAdminOut


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
