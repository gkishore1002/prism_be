from collections.abc import Generator
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import SessionLocal
from app.models.institution import Institution
from app.models.super_admin import SuperAdmin
from app.models.user import User
from app.services.platform_auth import (
    SYSTEM_INSTITUTION_ID,
    SUPER_USER_ROLE,
    is_super_user_token,
    is_system_org_code,
)
from app.services.tenant_context import (
    close_tenant_db,
    get_effective_institution_id,
    lookup_institution_by_code,
    open_tenant_db,
    set_request_tenant_context,
)

security = HTTPBearer(auto_error=False)


def _resolve_org_code(request: Request) -> str | None:
    code = request.headers.get("x-org-code") or request.query_params.get("org_code")
    if not code:
        return None
    code = code.strip().upper()
    return code if code and not is_system_org_code(code) else None


def get_public_db() -> Generator[Session, None, None]:
    """Unscoped session for public registry (institutions, system_initialization)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        close_tenant_db(db)


def _lookup_institution_schema(institution_id: str) -> tuple[str, str] | None:
    db = SessionLocal()
    try:
        institution = db.get(Institution, institution_id)
        if not institution:
            return None
        schema_name = getattr(institution, "schema_name", None) or "public"
        return schema_name, institution.id
    finally:
        close_tenant_db(db)


def get_db(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> Generator[Session, None, None]:
    """Tenant-bound session when Bearer JWT present; super users may pass X-Org-Code."""
    schema_name: str | None = None
    institution_id: str | None = None

    if credentials:
        payload = decode_access_token(credentials.credentials)
        if payload:
            from app.services.branch_access import set_request_admin_scope

            scope = payload.get("admin_scope")
            set_request_admin_scope(scope if isinstance(scope, str) else None)
            if is_super_user_token(payload):
                org_code = _resolve_org_code(request)
                if org_code:
                    lookup_db = SessionLocal()
                    try:
                        institution = lookup_institution_by_code(lookup_db, org_code)
                    finally:
                        close_tenant_db(lookup_db)
                    if not institution:
                        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
                    schema_name = institution.schema_name or "public"
                    institution_id = institution.id
            else:
                inst_id = payload.get("institution_id")
                if isinstance(inst_id, str) and inst_id != SYSTEM_INSTITUTION_ID:
                    resolved = _lookup_institution_schema(inst_id)
                    if resolved:
                        schema_name, institution_id = resolved

    set_request_tenant_context(request, schema_name=schema_name, institution_id=institution_id)

    if schema_name and institution_id:
        db = open_tenant_db(schema_name)
    else:
        db = SessionLocal()

    try:
        yield db
    finally:
        from app.services.branch_access import clear_request_admin_scope

        clear_request_admin_scope()
        close_tenant_db(db)


def get_token_payload(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any]:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_access_token(credentials.credentials)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return payload


def get_effective_role(payload: dict[str, Any], user: User) -> str:
    role = payload.get("role")
    return role if isinstance(role, str) else user.role


def _super_user_as_user(super_admin: SuperAdmin, institution_id: str) -> User:
    return User(
        id=super_admin.id,
        institution_id=institution_id,
        name=super_admin.full_name,
        email=super_admin.email,
        password_hash="",
        role="admin",
        roles="admin",
        is_owner=False,
    )


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    public_db: Session = Depends(get_public_db),
    payload: dict[str, Any] = Depends(get_token_payload),
) -> User:
    if is_super_user_token(payload):
        super_admin = public_db.get(SuperAdmin, payload["sub"])
        if not super_admin or not super_admin.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        inst_id = get_effective_institution_id(request, payload) or SYSTEM_INSTITUTION_ID
        return _super_user_as_user(super_admin, inst_id)

    user = db.get(User, payload["sub"])
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def require_roles(*roles: str):
    def checker(
        user: User = Depends(get_current_user),
        payload: dict[str, Any] = Depends(get_token_payload),
    ) -> User:
        effective = get_effective_role(payload, user)
        if effective == SUPER_USER_ROLE and "admin" in roles:
            return user
        if effective not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return checker
