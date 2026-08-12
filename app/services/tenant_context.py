"""Multi-schema tenant context — public.institutions registry + per-tenant Postgres schemas.

Swotify-style: JWT institution_id → public.institutions.schema_name → schema_translate_map.
SQLite dev mode: all tables stay in public (multi_schema_enabled=False).
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.institution import Institution

_tenant_schema: ContextVar[str | None] = ContextVar("tenant_schema", default=None)
_tenant_institution_id: ContextVar[str | None] = ContextVar("tenant_institution_id", default=None)


def schema_name_for_code(code: str) -> str:
    safe = re.sub(r"[^a-z0-9_]", "_", code.strip().lower())
    safe = safe.strip("_") or "org"
    return f"tenant_{safe}"


def is_multi_schema_enabled() -> bool:
    return settings.database_url.startswith("postgresql")


def get_current_tenant_schema() -> str | None:
    return _tenant_schema.get()


def get_current_institution_id() -> str | None:
    return _tenant_institution_id.get()


def set_request_tenant_context(
    request: Request,
    *,
    schema_name: str | None,
    institution_id: str | None,
) -> None:
    request.state.tenant_schema = schema_name
    request.state.tenant_institution_id = institution_id


def get_request_tenant_institution_id(request: Request) -> str | None:
    return getattr(request.state, "tenant_institution_id", None)


def get_effective_institution_id(request: Request, payload: dict[str, Any] | None = None) -> str | None:
    bound = get_request_tenant_institution_id(request)
    if bound:
        return bound
    if payload:
        inst_id = payload.get("institution_id")
        if isinstance(inst_id, str):
            return inst_id
    return get_current_institution_id()


def set_tenant_context(*, schema_name: str, institution_id: str) -> tuple[Any, Any]:
    return (
        _tenant_schema.set(schema_name),
        _tenant_institution_id.set(institution_id),
    )


def reset_tenant_context(tokens: tuple[Any, Any]) -> None:
    schema_token, inst_token = tokens
    _tenant_schema.reset(schema_token)
    _tenant_institution_id.reset(inst_token)


def safe_reset_tenant_context(tokens: tuple[Any, Any] | None) -> None:
    """Reset tenant ContextVars; ignore cross-context errors from FastAPI thread pools."""
    if not tokens:
        return
    try:
        reset_tenant_context(tokens)
    except ValueError:
        pass


def schema_translate_map(schema_name: str | None) -> dict[Any, str] | None:
    if not is_multi_schema_enabled() or not schema_name or schema_name == "public":
        return None
    return {None: schema_name}


def open_tenant_db(schema_name: str | None) -> Session:
    """Open a session with schema_translate_map applied before the first query."""
    from app.db.session import SessionLocal, engine

    translate = schema_translate_map(schema_name)
    if not translate:
        return SessionLocal()

    conn = engine.connect().execution_options(schema_translate_map=translate)
    db = Session(bind=conn)
    db.info["tenant_conn"] = conn
    return db


def close_tenant_db(db: Session) -> None:
    conn = db.info.pop("tenant_conn", None)
    db.close()
    if conn is not None:
        conn.close()


def bind_session_to_tenant(db: Session, schema_name: str | None) -> None:
    """Deprecated for request paths — use open_tenant_db. Kept for legacy call sites."""
    translate = schema_translate_map(schema_name)
    if translate and "tenant_conn" not in db.info:
        conn = db.get_bind().connect().execution_options(schema_translate_map=translate)
        db.info["tenant_conn"] = conn
        db.bind = conn


def lookup_institution_by_code(db: Session, code: str) -> Institution | None:
    return db.query(Institution).filter(Institution.code == code.strip().upper()).first()


def resolve_schema_for_institution(db: Session, institution_id: str) -> str:
    institution = db.get(Institution, institution_id)
    if not institution:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    schema = getattr(institution, "schema_name", None) or "public"
    if is_multi_schema_enabled() and not schema:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Organization schema not configured")
    return schema


def assert_jwt_institution_matches(request: Request, institution_id: str) -> None:
    """Reject cross-tenant remapping when JWT already binds an organization."""
    bound = get_effective_institution_id(request)
    if bound and bound != institution_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization context mismatch")
