"""First-run / provision organization — public registry + tenant schema."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal, engine
from app.db.tenant import create_tenant_schema
from app.models.institution import Institution
from app.models.user import User
from app.services.audit_log import record_audit
from app.services.deployment import is_deployment_initialized, mark_deployment_initialized, utc_now_iso
from app.services.institution_policies import save_institution_policies
from app.services.tenant_context import (
    close_tenant_db,
    is_multi_schema_enabled,
    open_tenant_db,
    safe_reset_tenant_context,
    schema_name_for_code,
    set_tenant_context,
)
from app.utils import to_json_list


class SetupError(Exception):
    pass


def provision_organization(
    public_db: Session,
    *,
    organization_name: str,
    organization_code: str,
    super_admin_name: str,
    super_admin_email: str,
    password: str,
    mark_initialized: bool = False,
    initialized_by_user_id: str | None = None,
) -> dict:
    """Create public.institutions row + tenant schema + Super Admin in tenant schema."""
    org_name = organization_name.strip()
    org_code = (organization_code or settings.default_organization_code).strip().upper()
    admin_name = super_admin_name.strip()
    admin_email = super_admin_email.strip().lower()

    if not org_name or not org_code or not admin_name or not admin_email or not password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="All setup fields are required")
    if len(password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters")

    if public_db.query(Institution).filter(Institution.code == org_code).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Organization code already in use")

    schema_name = schema_name_for_code(org_code) if is_multi_schema_enabled() else "public"
    if (
        is_multi_schema_enabled()
        and public_db.query(Institution).filter(Institution.schema_name == schema_name).first()
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Organization schema already exists")

    inst_id = f"inst-{uuid.uuid4().hex[:8]}"
    admin_id = f"adm-{uuid.uuid4().hex[:8]}"

    try:
        institution = Institution(
            id=inst_id,
            name=org_name,
            code=org_code,
            schema_name=schema_name,
            type="coaching",
            board_ids=to_json_list([]),
        )
        public_db.add(institution)
        public_db.flush()
        public_db.commit()
        public_db.refresh(institution)

        create_tenant_schema(engine, schema_name)

        if is_multi_schema_enabled():
            tokens = set_tenant_context(schema_name=schema_name, institution_id=inst_id)
            db = open_tenant_db(schema_name)
        else:
            tokens = None
            db = public_db
        try:
            if db.query(User).filter(User.email == admin_email).first():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")

            admin = User(
                id=admin_id,
                institution_id=inst_id,
                name=admin_name,
                email=admin_email,
                password_hash=hash_password(password),
                role="admin",
                roles="admin",
                is_owner=True,
            )
            db.add(admin)
            db.flush()
            save_institution_policies(db, inst_id)

            record_audit(
                db,
                institution_id=inst_id,
                actor_user_id=admin_id,
                actor_role="admin",
                action="organization_provision",
                entity_type="organization",
                entity_id=inst_id,
                new_state={
                    "organizationName": org_name,
                    "organizationCode": org_code,
                    "schemaName": schema_name,
                    "superAdminEmail": admin_email,
                },
            )
            db.commit()
        finally:
            if is_multi_schema_enabled():
                close_tenant_db(db)
                safe_reset_tenant_context(tokens)

        if mark_initialized and initialized_by_user_id:
            mark_deployment_initialized(public_db, user_id=initialized_by_user_id)

        public_db.commit()
        public_db.refresh(institution)

        return {
            "organization": {
                "id": institution.id,
                "name": institution.name,
                "code": institution.code,
                "schemaName": institution.schema_name,
            },
            "superAdmin": {"id": admin_id, "name": admin_name, "email": admin_email},
        }
    except HTTPException:
        public_db.rollback()
        raise
    except Exception as exc:
        public_db.rollback()
        raise SetupError(str(exc)) from exc


def run_first_run_setup(
    public_db: Session,
    *,
    organization_name: str,
    organization_code: str,
    super_admin_name: str,
    super_admin_phone: str,
    password: str | None,
) -> dict:
    from app.services.user_credentials import resolve_user_credentials

    if is_deployment_initialized(public_db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Deployment is already initialized")

    owner_email, owner_password = resolve_user_credentials(phone=super_admin_phone, password=password)
    result = provision_organization(
        public_db,
        organization_name=organization_name,
        organization_code=organization_code,
        super_admin_name=super_admin_name,
        super_admin_email=owner_email,
        password=owner_password,
    )
    mark_deployment_initialized(public_db, user_id=result["superAdmin"]["id"])
    public_db.commit()
    return result
