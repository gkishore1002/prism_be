"""Organization + branch access authorization.

Internal model: Institution = tenant, Center = branch.
"""

from __future__ import annotations

from contextvars import ContextVar

from fastapi import HTTPException, status
from sqlalchemy.orm import Query, Session

from app.models.branch_access import UserCenterAccess
from app.models.institution import Center
from app.models.user import StudentProfile, User


from app.services.platform_auth import SUPER_USER_ROLE

_admin_scope: ContextVar[str | None] = ContextVar("admin_scope", default=None)


def set_request_admin_scope(scope: str | None) -> None:
    if scope in ("organization", "branch"):
        _admin_scope.set(scope)
    else:
        _admin_scope.set(None)


def get_request_admin_scope() -> str | None:
    return _admin_scope.get()


def clear_request_admin_scope() -> None:
    _admin_scope.set(None)


def is_platform_super_user(role: str) -> bool:
    return role == SUPER_USER_ROLE


def is_organization_owner(user: User, role: str) -> bool:
    """Tenant organization owner (admin + is_owner). Not the platform super user."""
    return role == "admin" and bool(getattr(user, "is_owner", False))


def has_tenant_management_access(user: User, role: str) -> bool:
    """May manage tenant-wide settings (org owner or platform super user in org context)."""
    return is_organization_owner(user, role) or is_platform_super_user(role)


def assigned_center_ids(db: Session, user_id: str) -> list[str]:
    rows = db.query(UserCenterAccess.center_id).filter(UserCenterAccess.user_id == user_id).all()
    return [row[0] for row in rows]


def has_organization_wide_branch_access(db: Session, user: User, role: str) -> bool:
    if role == "admin" and get_request_admin_scope() == "branch":
        return False
    if is_platform_super_user(role):
        return True
    if is_organization_owner(user, role):
        return True
    if role == "tutor" and not assigned_center_ids(db, user.id):
        return True
    return False


def assert_actor_can_assign_centers(
    db: Session,
    actor: User,
    role: str,
    center_ids: list[str],
) -> None:
    """Branch admins may only assign branches they can access."""
    if has_tenant_management_access(actor, role):
        return
    accessible = get_accessible_center_ids(db, actor, role)
    if accessible is None:
        return
    invalid = set(center_ids) - set(accessible)
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot assign branches outside your access",
        )


def get_accessible_center_ids(db: Session, user: User, role: str) -> list[str] | None:
    """Return accessible center ids, or None when all org branches are allowed."""
    if is_platform_super_user(role):
        return None

    if role == "student":
        profile = (
            db.query(StudentProfile)
            .filter(StudentProfile.user_id == user.id)
            .first()
        )
        if not profile or not profile.center_id:
            return []
        return [profile.center_id]

    if has_organization_wide_branch_access(db, user, role):
        return None

    ids = assigned_center_ids(db, user.id)
    if role == "admin" and not ids:
        if is_organization_owner(user, role):
            return None
        return []
    if not ids:
        return None

    rows = (
        db.query(Center.id)
        .filter(Center.institution_id == user.institution_id, Center.id.in_(ids))
        .all()
    )
    return [row[0] for row in rows]


def center_belongs_to_organization(db: Session, center_id: str, institution_id: str) -> bool:
    center = db.get(Center, center_id)
    return bool(center and center.institution_id == institution_id)


def can_access_center(db: Session, user: User, role: str, center_id: str) -> bool:
    if not center_belongs_to_organization(db, center_id, user.institution_id):
        return False
    accessible = get_accessible_center_ids(db, user, role)
    if accessible is None:
        return True
    return center_id in accessible


def assert_can_access_center(db: Session, user: User, role: str, center_id: str) -> None:
    if not can_access_center(db, user, role, center_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Branch access denied")


def assessment_matches_branch_scope(center_ids_raw: str | None, scope: list[str] | None) -> bool:
    """True when an assessment belongs in the resolved branch filter."""
    if scope is None:
        return True
    if not scope:
        return False
    import json

    try:
        assigned = json.loads(center_ids_raw or "[]")
    except json.JSONDecodeError:
        assigned = []
    if not isinstance(assigned, list) or not assigned:
        return True
    return bool(set(assigned) & set(scope))


def user_matches_center_scope(db: Session, staff: User, scope: list[str] | None) -> bool:
    """True when a staff member is linked to the resolved branch filter."""
    if scope is None:
        return True
    if not scope:
        return False
    assigned = assigned_center_ids(db, staff.id)
    if not assigned:
        return True
    return bool(set(assigned) & set(scope))


def resolve_branch_filter(
    db: Session,
    user: User,
    role: str,
    requested_center_id: str | None,
) -> list[str] | None:
    """Resolve which center ids may be queried.

    None → all centers the user may access (org-wide for owner / unscoped tutor).
    [] → no accessible branches.
    [id, ...] → explicit branch scope.
    """
    if requested_center_id:
        assert_can_access_center(db, user, role, requested_center_id)
        return [requested_center_id]
    return get_accessible_center_ids(db, user, role)


def apply_branch_scope_to_students(
    q: Query,
    db: Session,
    user: User,
    role: str,
    requested_center_id: str | None = None,
) -> Query:
    scope = resolve_branch_filter(db, user, role, requested_center_id)
    if scope is None:
        return q
    if not scope:
        return q.filter(StudentProfile.id == "__none__")
    return q.filter(StudentProfile.center_id.in_(scope))


def assert_can_access_student(db: Session, user: User, role: str, profile: StudentProfile) -> None:
    if profile.user.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    if role == "student" and profile.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    if role in ("admin", "tutor") and profile.center_id:
        if not can_access_center(db, user, role, profile.center_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Branch access denied")


def validate_center_assignment(
    db: Session,
    user: User,
    center_id: str,
    *,
    institution_id: str | None = None,
) -> Center:
    org_id = institution_id or user.institution_id
    center = db.get(Center, center_id)
    if not center or center.institution_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")
    return center


def set_user_center_access(
    db: Session,
    *,
    user: User,
    center_ids: list[str],
    actor: User,
) -> list[str]:
    """Replace branch assignments for a user. Validates same-organization."""
    unique_ids = list(dict.fromkeys(center_ids))
    for center_id in unique_ids:
        validate_center_assignment(db, user, center_id, institution_id=user.institution_id)

    db.query(UserCenterAccess).filter(UserCenterAccess.user_id == user.id).delete()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    import uuid

    for center_id in unique_ids:
        db.add(
            UserCenterAccess(
                id=f"uca-{uuid.uuid4().hex[:8]}",
                user_id=user.id,
                center_id=center_id,
                created_at=now,
                created_by=actor.id,
            )
        )
    return unique_ids


def require_organization_owner(user: User, role: str) -> None:
    if not is_organization_owner(user, role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization owner access required",
        )


def require_tenant_management_access(user: User, role: str) -> None:
    if not has_tenant_management_access(user, role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization owner or platform super user access required",
        )
