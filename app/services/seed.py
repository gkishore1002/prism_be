"""Idempotent platform + demo tenant seed (Swotify master_bootstrap equivalent)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.institution import Institution
from app.models.super_admin import SuperAdmin
from app.services.deployment import is_deployment_initialized, mark_deployment_initialized
from app.services.platform_auth import SYSTEM_INSTITUTION_ID
from app.services.setup import provision_organization

logger = logging.getLogger(__name__)

SUPER_ADMIN_ID = "sup-1"
DEMO_INST_CODE = "DEMO001"


def _upsert_super_admin(public_db: Session) -> SuperAdmin:
    email = settings.seed_super_admin_email.strip().lower()
    row = public_db.query(SuperAdmin).filter(SuperAdmin.email == email).first()
    pwd_hash = hash_password(settings.seed_super_admin_password)
    if row:
        row.password_hash = pwd_hash
        row.full_name = settings.seed_super_admin_name
        row.is_active = True
        public_db.flush()
        return row

    row = SuperAdmin(
        id=SUPER_ADMIN_ID,
        email=email,
        password_hash=pwd_hash,
        full_name=settings.seed_super_admin_name,
        is_active=True,
    )
    public_db.add(row)
    public_db.flush()
    return row


def _seed_demo_institution(public_db: Session) -> Institution | None:
    code = settings.seed_demo_org_code.strip().upper()
    existing = public_db.query(Institution).filter(Institution.code == code).first()
    if existing:
        return existing

    provision_organization(
        public_db,
        organization_name=settings.seed_demo_org_name,
        organization_code=code,
        super_admin_name=settings.seed_demo_admin_name,
        super_admin_email=settings.seed_demo_admin_email,
        password=settings.seed_demo_password,
    )
    return public_db.query(Institution).filter(Institution.code == code).first()


def _seed_demo_extra_users(public_db: Session, institution: Institution) -> None:
    """Add tutor + student to demo tenant if missing."""
    from app.db.session import SessionLocal
    from app.models.institution import Center
    from app.models.user import StudentProfile, User
    from app.services.tenant_context import close_tenant_db, is_multi_schema_enabled, open_tenant_db, safe_reset_tenant_context, set_tenant_context

    schema_name = institution.schema_name or "public"
    if is_multi_schema_enabled():
        tokens = set_tenant_context(schema_name=schema_name, institution_id=institution.id)
        db = open_tenant_db(schema_name)
    else:
        tokens = None
        db = public_db

    try:
        pwd = hash_password(settings.seed_demo_password)
        center = db.query(Center).filter(Center.institution_id == institution.id).first()
        if not center:
            center = Center(
                id=f"ctr-{institution.id}-hq",
                institution_id=institution.id,
                name=f"{institution.name} · HQ",
                city="",
            )
            db.add(center)
            db.flush()

        tutor_email = settings.seed_demo_tutor_email.strip().lower()
        if not db.query(User).filter(User.email == tutor_email).first():
            db.add(
                User(
                    id=f"tut-{uuid.uuid4().hex[:8]}",
                    institution_id=institution.id,
                    name=settings.seed_demo_tutor_name,
                    email=tutor_email,
                    password_hash=pwd,
                    role="tutor",
                    roles="tutor",
                )
            )

        student_email = settings.seed_demo_student_email.strip().lower()
        if not db.query(User).filter(User.email == student_email).first():
            sid = f"stu-{uuid.uuid4().hex[:8]}"
            db.add(
                User(
                    id=sid,
                    institution_id=institution.id,
                    name=settings.seed_demo_student_name,
                    email=student_email,
                    password_hash=pwd,
                    role="student",
                    roles="student",
                )
            )
            db.add(
                StudentProfile(
                    id=sid,
                    user_id=sid,
                    board="",
                    grade="",
                    batch="",
                    center_id=center.id,
                )
            )

        if is_multi_schema_enabled():
            db.commit()
        else:
            db.flush()
    finally:
        if is_multi_schema_enabled():
            close_tenant_db(db)
            safe_reset_tenant_context(tokens)


def seed_demo_platform() -> bool:
    """Create platform super admin + demo org (idempotent). Returns True if anything changed."""
    if not settings.seed_demo:
        return False

    public_db = SessionLocal()
    changed = False
    try:
        super_admin = _upsert_super_admin(public_db)
        changed = True

        demo_inst = _seed_demo_institution(public_db)
        if demo_inst:
            _seed_demo_extra_users(public_db, demo_inst)
            changed = True

        if not is_deployment_initialized(public_db):
            mark_deployment_initialized(public_db, user_id=super_admin.id)
            changed = True

        public_db.commit()
        if changed:
            logger.info(
                "Demo platform seed ready — super user %s (SYSTEM), demo org %s",
                super_admin.email,
                settings.seed_demo_org_code.upper(),
            )
        return changed
    except Exception:
        public_db.rollback()
        logger.exception("Demo platform seed failed")
        raise
    finally:
        public_db.close()


def print_seed_credentials() -> None:
    """Log demo credentials (development only)."""
    print()
    print("=" * 60)
    print("PRISM SEED / DEMO CREDENTIALS (development & local testing)")
    print("=" * 60)
    print()
    print("Platform Super User (org code SYSTEM):")
    print(f"  Email    : {settings.seed_super_admin_email}")
    print(f"  Password : {settings.seed_super_admin_password}")
    print(f"  Org code : SYSTEM")
    print()
    print(f"Demo organization ({settings.seed_demo_org_code.upper()}):")
    print(f"  Org Admin : {settings.seed_demo_admin_email} / {settings.seed_demo_password}")
    print(f"  Tutor     : {settings.seed_demo_tutor_email} / {settings.seed_demo_password}")
    print(f"  Student   : {settings.seed_demo_student_email} / {settings.seed_demo_password}")
    print()
    print("Login at http://localhost:5174/login with Email + Password + Organization code.")
    print("Super users: switch org via X-Org-Code header or org picker in the admin UI.")
    print("=" * 60)
    print()
