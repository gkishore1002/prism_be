"""Deployment initialization helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.deployment import SINGLETON_ID, SystemInitialization
from app.models.institution import Institution


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def is_deployment_initialized(db: Session) -> bool:
    row = db.get(SystemInitialization, SINGLETON_ID)
    if row is not None:
        return True
    # Legacy databases created before system_initialization existed.
    return db.query(Institution).first() is not None


def mark_deployment_initialized(db: Session, *, user_id: str) -> SystemInitialization:
    existing = db.get(SystemInitialization, SINGLETON_ID)
    if existing is not None:
        return existing
    row = SystemInitialization(
        id=SINGLETON_ID,
        initialized_at=utc_now_iso(),
        initialized_by_user_id=user_id,
    )
    db.add(row)
    db.flush()
    return row


def backfill_initialization_for_legacy(db: Session) -> bool:
    """Persist initialization marker for databases created before system_initialization."""
    if db.get(SystemInitialization, SINGLETON_ID) is not None:
        return False
    inst = db.query(Institution).first()
    if not inst:
        return False
    from app.models.user import User

    super_admin = (
        db.query(User)
        .filter(User.institution_id == inst.id, User.role == "admin", User.is_owner.is_(True))
        .first()
    )
    user_id = super_admin.id if super_admin else "legacy"
    mark_deployment_initialized(db, user_id=user_id)
    db.commit()
    return True
