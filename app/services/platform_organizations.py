"""Platform-level organization management for super users."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.institution import Institution
from app.models.user import User
from app.schemas import (
    PlatformOrganizationAdminOut,
    PlatformOrganizationDetailOut,
    PlatformOrganizationOut,
    PlatformOrganizationOwnerOut,
)
from app.services.setup import provision_organization
from app.services.tenant_context import (
    close_tenant_db,
    is_multi_schema_enabled,
    lookup_institution_by_code,
    open_tenant_db,
    safe_reset_tenant_context,
    set_tenant_context,
)


def organization_out(inst: Institution, *, admin_count: int = 0) -> PlatformOrganizationOut:
    return PlatformOrganizationOut(
        id=inst.id,
        name=inst.name,
        code=inst.code,
        schema_name=getattr(inst, "schema_name", None) or "public",
        type=inst.type,
        is_active=bool(getattr(inst, "is_active", True)),
        admin_count=admin_count,
    )


def _with_tenant_session(public_db: Session, institution: Institution):
    """Bind a tenant session for reads inside an organization schema."""
    if is_multi_schema_enabled():
        schema = institution.schema_name
        tokens = set_tenant_context(schema_name=schema, institution_id=institution.id)
        db = open_tenant_db(schema)
        return db, tokens, True
    return public_db, None, False


def _fetch_organization_admins(public_db: Session, institution: Institution) -> list[PlatformOrganizationAdminOut]:
    db, tokens, owned = _with_tenant_session(public_db, institution)
    try:
        rows = (
            db.query(User)
            .filter(User.institution_id == institution.id, User.role == "admin")
            .order_by(User.is_owner.desc(), User.name)
            .all()
        )
        return [
            PlatformOrganizationAdminOut(
                id=row.id,
                name=row.name,
                email=row.email,
                is_owner=bool(row.is_owner),
            )
            for row in rows
        ]
    finally:
        if owned:
            close_tenant_db(db)
            safe_reset_tenant_context(tokens)


def _count_organization_admins(public_db: Session, institution: Institution) -> int:
    return len(_fetch_organization_admins(public_db, institution))


def _fetch_organization_owner(public_db: Session, institution: Institution) -> User | None:
    if is_multi_schema_enabled():
        schema = institution.schema_name
        tokens = set_tenant_context(schema_name=schema, institution_id=institution.id)
        db = open_tenant_db(schema)
        try:
            return (
                db.query(User)
                .filter(User.institution_id == institution.id, User.is_owner.is_(True))
                .order_by(User.id)
                .first()
            )
        finally:
            close_tenant_db(db)
            safe_reset_tenant_context(tokens)
    return (
        public_db.query(User)
        .filter(User.institution_id == institution.id, User.is_owner.is_(True))
        .order_by(User.id)
        .first()
    )


def get_organization_detail(public_db: Session, code: str) -> PlatformOrganizationDetailOut:
    institution = lookup_institution_by_code(public_db, code)
    if not institution:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    owner = _fetch_organization_owner(public_db, institution)
    admins = _fetch_organization_admins(public_db, institution)
    base = organization_out(institution, admin_count=len(admins))
    return PlatformOrganizationDetailOut(
        **base.model_dump(),
        owner=PlatformOrganizationOwnerOut(name=owner.name, email=owner.email) if owner else None,
        admins=admins,
    )


def create_organization(
    public_db: Session,
    *,
    organization_name: str,
    organization_code: str,
    owner_name: str,
    owner_phone: str,
    password: str | None,
    org_type: str = "coaching",
) -> PlatformOrganizationOut:
    from app.services.user_credentials import resolve_user_credentials

    owner_email, owner_password = resolve_user_credentials(phone=owner_phone, password=password)
    result = provision_organization(
        public_db,
        organization_name=organization_name,
        organization_code=organization_code,
        super_admin_name=owner_name,
        super_admin_email=owner_email,
        password=owner_password,
        mark_initialized=False,
    )
    institution = public_db.get(Institution, result["organization"]["id"])
    if not institution:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Organization not found after create")
    if org_type and org_type.strip() and org_type.strip() != institution.type:
        institution.type = org_type.strip()
        public_db.commit()
        public_db.refresh(institution)
    return organization_out(institution)


def update_organization(
    public_db: Session,
    code: str,
    *,
    name: str | None = None,
    org_type: str | None = None,
    is_active: bool | None = None,
) -> PlatformOrganizationOut:
    institution = lookup_institution_by_code(public_db, code)
    if not institution:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    if name is not None:
        institution.name = name.strip()
    if org_type is not None:
        institution.type = org_type.strip()
    if is_active is not None:
        institution.is_active = is_active
    public_db.commit()
    public_db.refresh(institution)
    return organization_out(institution)
