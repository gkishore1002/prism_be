"""Organization admin management — branch access assignment."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_effective_role, get_token_payload, require_roles
from app.core.routing import CamelCaseAPIRoute
from app.core.security import hash_password
from app.models.branch_access import UserCenterAccess
from app.models.user import User
from app.schemas import AdminBranchAccessUpdate, AdminCreate, AdminUpdate, AdminUserOut
from app.services.audit_log import record_audit
from app.services.branch_access import (
    assigned_center_ids,
    require_tenant_management_access,
    set_user_center_access,
)
from app.services.user_credentials import resolve_user_credentials
from app.services.user_roles import add_role, filter_users_with_role, has_role, is_admin_account, parse_roles

router = APIRouter(prefix="/admins", tags=["admins"], route_class=CamelCaseAPIRoute)


def _admin_out(db: Session, user: User) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        name=user.name,
        email=user.email,
        is_owner=bool(getattr(user, "is_owner", False)),
        active=True,
        center_ids=assigned_center_ids(db, user.id),
        roles=parse_roles(user),
    )


def _get_admin(db: Session, admin_id: str, institution_id: str) -> User:
    admin = db.get(User, admin_id)
    if not admin or admin.institution_id != institution_id or not is_admin_account(admin):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin not found")
    return admin


@router.get("", response_model=list[AdminUserOut])
def list_admins(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
    payload: dict = Depends(get_token_payload),
) -> list[AdminUserOut]:
    role = get_effective_role(payload, user)
    require_tenant_management_access(user, role)
    admins = (
        filter_users_with_role(
            db.query(User).filter(User.institution_id == user.institution_id),
            "admin",
        )
        .order_by(User.name)
        .all()
    )
    return [_admin_out(db, a) for a in admins]


@router.post("", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
def create_admin(
    body: AdminCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
    payload: dict = Depends(get_token_payload),
) -> AdminUserOut:
    role = get_effective_role(payload, user)
    require_tenant_management_access(user, role)

    email, password = resolve_user_credentials(phone=body.phone, password=body.password)
    existing = db.query(User).filter(User.email == email).first()

    if existing:
        if existing.institution_id != user.institution_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Phone number already registered")
        if has_role(existing, "student") and not has_role(existing, "admin"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This account is a student profile. Create a separate staff phone number.",
            )
        add_role(existing, "admin")
        if body.is_owner:
            existing.is_owner = True
        if body.also_tutor:
            add_role(existing, "tutor")
        if body.name.strip():
            existing.name = body.name.strip()
        admin = existing
    else:
        initial_roles = ["admin"]
        if body.also_tutor:
            initial_roles.append("tutor")
        admin = User(
            id=f"adm-{uuid.uuid4().hex[:8]}",
            institution_id=user.institution_id,
            name=body.name.strip(),
            email=email,
            password_hash=hash_password(password),
            role="admin",
            roles=",".join(initial_roles),
            is_owner=body.is_owner,
        )
        db.add(admin)
        db.flush()

    if not admin.is_owner and body.center_ids:
        set_user_center_access(db, user=admin, center_ids=body.center_ids, actor=user)

    record_audit(
        db,
        institution_id=user.institution_id,
        actor_user_id=user.id,
        actor_role=role,
        action="admin_create",
        entity_type="user",
        entity_id=admin.id,
        new_state={
            "email": admin.email,
            "isOwner": admin.is_owner,
            "centerIds": body.center_ids,
            "roles": parse_roles(admin),
        },
    )
    db.commit()
    db.refresh(admin)
    return _admin_out(db, admin)


@router.patch("/{admin_id}", response_model=AdminUserOut)
def update_admin(
    admin_id: str,
    body: AdminUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
    payload: dict = Depends(get_token_payload),
) -> AdminUserOut:
    role = get_effective_role(payload, user)
    require_tenant_management_access(user, role)
    admin = _get_admin(db, admin_id, user.institution_id)

    if body.email is not None:
        email = body.email.strip().lower()
        conflict = db.query(User).filter(User.email == email, User.id != admin_id).first()
        if conflict:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
        admin.email = email
    if body.name is not None:
        admin.name = body.name.strip()
    if body.is_owner is not None:
        admin.is_owner = body.is_owner
        if body.is_owner:
            db.query(UserCenterAccess).filter(UserCenterAccess.user_id == admin.id).delete()

    db.commit()
    db.refresh(admin)
    return _admin_out(db, admin)


@router.put("/{admin_id}/branches", response_model=AdminUserOut)
def set_admin_branches(
    admin_id: str,
    body: AdminBranchAccessUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
    payload: dict = Depends(get_token_payload),
) -> AdminUserOut:
    role = get_effective_role(payload, user)
    require_tenant_management_access(user, role)
    admin = _get_admin(db, admin_id, user.institution_id)
    if admin.is_owner:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization owners have access to all branches",
        )

    prev = assigned_center_ids(db, admin.id)
    center_ids = set_user_center_access(db, user=admin, center_ids=body.center_ids, actor=user)
    record_audit(
        db,
        institution_id=user.institution_id,
        actor_user_id=user.id,
        actor_role=role,
        action="admin_branch_access_update",
        entity_type="user",
        entity_id=admin.id,
        previous_state={"centerIds": prev},
        new_state={"centerIds": center_ids},
    )
    db.commit()
    db.refresh(admin)
    return _admin_out(db, admin)
