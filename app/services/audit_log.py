"""Append-only audit logging for sensitive operations."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditLog


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def record_audit(
    db: Session,
    *,
    institution_id: str,
    actor_user_id: str,
    actor_role: str,
    action: str,
    entity_type: str,
    entity_id: str,
    previous_state: Any = None,
    new_state: Any = None,
    notes: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        id=f"aud-{uuid.uuid4().hex[:10]}",
        institution_id=institution_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        previous_state=_serialize(previous_state),
        new_state=_serialize(new_state),
        notes=notes,
        created_at=_now_iso(),
    )
    db.add(entry)
    return entry
