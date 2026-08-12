"""Unified staff management — admins, branch admins, and tutors in one place."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_effective_role, get_token_payload, require_roles
from app.core.routing import CamelCaseAPIRoute
from app.core.security import hash_password
from app.models.branch_access import UserCenterAccess
from app.models.user import User
from app.schemas import StaffBranchUpdate, StaffCreate, StaffOut, StaffUpdate
from app.services.audit_log import record_audit
from app.services.branch_access import (
    assigned_center_ids,
    assert_actor_can_assign_centers,
    has_tenant_management_access,
    require_tenant_management_access,
    set_user_center_access,
)
from app.services.user_credentials import resolve_user_credentials
from app.services.user_roles import (
    add_role,
    has_role,
    is_admin_account,
    is_tutor_account,
    parse_roles,
    role_filter,
    set_roles,
)

router = APIRouter(prefix="/staff", tags=["staff"], route_class=CamelCaseAPIRoute)


def _staff_out(db: Session, user: User) -> StaffOut:
    return StaffOut(
        id=user.id,
        name=user.name,
        email=user.email,
        is_owner=bool(getattr(user, "is_owner", False)),
        active=True,
        center_ids=assigned_center_ids(db, user.id),
        roles=parse_roles(user),
    )


def _get_staff(db: Session, staff_id: str, institution_id: str) -> User:
    user = db.get(User, staff_id)
    if not user or user.institution_id != institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff member not found")
    if not is_admin_account(user) and not is_tutor_account(user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff member not found")
    return user


def _roles_from_create(body: StaffCreate) -> list[str]:
    roles: list[str] = []
    if body.is_owner or body.is_branch_admin:
        roles.append("admin")
    if body.is_tutor:
        roles.append("tutor")
    return roles


def _assert_create_permissions(body: StaffCreate, actor: User, role: str) -> None:
    roles = _roles_from_create(body)
    if not roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select at least one role: branch admin, organization owner, or tutor.",
        )
    if body.is_owner:
        require_tenant_management_access(actor, role)
    if body.is_branch_admin and not has_tenant_management_access(actor, role):
        pass  # branch admins may create other branch admins in their branches


@router.get("", response_model=list[StaffOut])
def list_staff(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
) -> list[StaffOut]:
    rows = (
        db.query(User)
        .filter(
            User.institution_id == user.institution_id,
            or_(role_filter("admin"), role_filter("tutor")),
        )
        .order_by(User.name)
        .all()
    )
    return [_staff_out(db, row) for row in rows]


@router.post("", response_model=StaffOut, status_code=status.HTTP_201_CREATED)
def create_staff(
    body: StaffCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
    payload: dict = Depends(get_token_payload),
) -> StaffOut:
    role = get_effective_role(payload, user)
    _assert_create_permissions(body, user, role)

    target_roles = _roles_from_create(body)
    if body.center_ids:
        assert_actor_can_assign_centers(db, user, role, body.center_ids)

    email, password = resolve_user_credentials(phone=body.phone, password=body.password)
    existing = db.query(User).filter(User.email == email).first()

    if existing:
        if existing.institution_id != user.institution_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Phone number already registered")
        if has_role(existing, "student") and not (has_role(existing, "admin") or has_role(existing, "tutor")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This account is a student profile. Use a separate staff phone number.",
            )
        for staff_role in target_roles:
            add_role(existing, staff_role)
        if body.is_owner:
            require_tenant_management_access(user, role)
            existing.is_owner = True
        if body.name.strip():
            existing.name = body.name.strip()
        staff = existing
    else:
        staff = User(
            id=f"stf-{uuid.uuid4().hex[:8]}",
            institution_id=user.institution_id,
            name=body.name.strip(),
            email=email,
            password_hash=hash_password(password),
            role=target_roles[0],
            roles=",".join(target_roles),
            is_owner=body.is_owner,
        )
        db.add(staff)
        db.flush()

    if body.center_ids and ("admin" in target_roles or "tutor" in target_roles):
        set_user_center_access(db, user=staff, center_ids=body.center_ids, actor=user)

    record_audit(
        db,
        institution_id=user.institution_id,
        actor_user_id=user.id,
        actor_role=role,
        action="staff_create",
        entity_type="user",
        entity_id=staff.id,
        new_state={
            "email": staff.email,
            "isOwner": staff.is_owner,
            "centerIds": body.center_ids,
            "roles": parse_roles(staff),
        },
    )
    db.commit()
    db.refresh(staff)
    return _staff_out(db, staff)


@router.patch("/{staff_id}", response_model=StaffOut)
def update_staff(
    staff_id: str,
    body: StaffUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
    payload: dict = Depends(get_token_payload),
) -> StaffOut:
    role = get_effective_role(payload, user)
    staff = _get_staff(db, staff_id, user.institution_id)

    role_change = body.is_owner is not None or body.is_branch_admin is not None or body.is_tutor is not None
    if body.email is not None:
        email = body.email.strip().lower()
        conflict = db.query(User).filter(User.email == email, User.id != staff_id).first()
        if conflict:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
        staff.email = email
    if body.name is not None:
        staff.name = body.name.strip()

    if role_change:
        if body.is_owner is not None:
            require_tenant_management_access(user, role)
        elif body.is_branch_admin is not None and not has_tenant_management_access(user, role):
            pass
        elif not has_tenant_management_access(user, role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Organization owner access required to change staff roles",
            )

        current = parse_roles(staff)
        is_admin = "admin" in current
        is_tutor = "tutor" in current

        if body.is_owner is not None:
            staff.is_owner = body.is_owner
            if body.is_owner:
                is_admin = True
        if body.is_branch_admin is not None:
            is_admin = body.is_branch_admin or staff.is_owner
        if body.is_tutor is not None:
            is_tutor = body.is_tutor

        next_roles: list[str] = []
        if is_admin or staff.is_owner:
            next_roles.append("admin")
        if is_tutor:
            next_roles.append("tutor")
        if not next_roles:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Staff must keep at least one role")
        try:
            set_roles(staff, next_roles)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        if not is_admin and not staff.is_owner and "admin" in current:
            staff.is_owner = False

    if body.center_ids is not None:
        assert_actor_can_assign_centers(db, user, role, body.center_ids)
        set_user_center_access(db, user=staff, center_ids=body.center_ids, actor=user)

    db.commit()
    db.refresh(staff)
    return _staff_out(db, staff)


@router.put("/{staff_id}/branches", response_model=StaffOut)
def set_staff_branches(
    staff_id: str,
    body: StaffBranchUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
    payload: dict = Depends(get_token_payload),
) -> StaffOut:
    role = get_effective_role(payload, user)
    staff = _get_staff(db, staff_id, user.institution_id)

    assert_actor_can_assign_centers(db, user, role, body.center_ids)

    prev = assigned_center_ids(db, staff.id)
    center_ids = set_user_center_access(db, user=staff, center_ids=body.center_ids, actor=user)
    record_audit(
        db,
        institution_id=user.institution_id,
        actor_user_id=user.id,
        actor_role=role,
        action="staff_branch_access_update",
        entity_type="user",
        entity_id=staff.id,
        previous_state={"centerIds": prev},
        new_state={"centerIds": center_ids},
    )
    db.commit()
    db.refresh(staff)
    return _staff_out(db, staff)
