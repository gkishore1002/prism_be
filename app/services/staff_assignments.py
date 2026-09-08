"""Staff academic-year assignments (mirror of student enrollments)."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.branch_access import UserCenterAccess
from app.models.enrollment import AcademicYear
from app.models.institution import Center
from app.models.staff_assignment import StaffAssignment
from app.models.user import User
from app.services.enrollments import get_current_academic_year
from app.services.user_roles import is_admin_account, is_tutor_account

ASSIGNMENT_STATUSES = frozenset({"active", "completed", "transferred", "inactive"})


def _today() -> str:
    return date.today().isoformat()


def _new_id() -> str:
    return f"sas-{uuid.uuid4().hex[:10]}"


def get_assignment_for_year(
    db: Session, staff_id: str, academic_year_id: str
) -> StaffAssignment | None:
    return (
        db.query(StaffAssignment)
        .filter(
            StaffAssignment.staff_id == staff_id,
            StaffAssignment.academic_year_id == academic_year_id,
        )
        .first()
    )


def list_assignments_for_staff(db: Session, staff_id: str) -> list[StaffAssignment]:
    return (
        db.query(StaffAssignment)
        .filter(StaffAssignment.staff_id == staff_id)
        .order_by(StaffAssignment.start_date.desc(), StaffAssignment.id.desc())
        .all()
    )


def list_assignments_for_year(
    db: Session,
    institution_id: str,
    academic_year_id: str,
    *,
    center_id: str | None = None,
) -> list[StaffAssignment]:
    q = db.query(StaffAssignment).filter(
        StaffAssignment.institution_id == institution_id,
        StaffAssignment.academic_year_id == academic_year_id,
    )
    if center_id:
        q = q.filter(StaffAssignment.center_id == center_id)
    return q.order_by(StaffAssignment.staff_id).all()


def _assert_center(db: Session, institution_id: str, center_id: str) -> Center:
    center = db.get(Center, center_id)
    if not center or center.institution_id != institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid center")
    return center


def _assert_year(db: Session, institution_id: str, academic_year_id: str) -> AcademicYear:
    year = db.get(AcademicYear, academic_year_id)
    if not year or year.institution_id != institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Academic year not found")
    return year


def upsert_assignment(
    db: Session,
    *,
    staff: User,
    academic_year_id: str,
    center_id: str,
    status_value: str = "active",
    start_date: str | None = None,
    end_date: str | None = None,
) -> StaffAssignment:
    """Create or update the assignment for this staff + year only."""
    if status_value not in ASSIGNMENT_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status. Allowed: {', '.join(sorted(ASSIGNMENT_STATUSES))}",
        )
    _assert_year(db, staff.institution_id, academic_year_id)
    _assert_center(db, staff.institution_id, center_id)

    row = get_assignment_for_year(db, staff.id, academic_year_id)
    if row:
        row.center_id = center_id
        row.status = status_value
        if start_date is not None:
            row.start_date = start_date
        if end_date is not None:
            row.end_date = end_date or None
        elif status_value == "active":
            row.end_date = None
        db.flush()
        return row

    row = StaffAssignment(
        id=_new_id(),
        staff_id=staff.id,
        institution_id=staff.institution_id,
        academic_year_id=academic_year_id,
        center_id=center_id,
        status=status_value,
        start_date=start_date or _today(),
        end_date=end_date or None,
    )
    db.add(row)
    db.flush()
    return row


def close_assignment(
    db: Session,
    assignment: StaffAssignment,
    *,
    end_date: str | None = None,
    status_value: str = "completed",
) -> StaffAssignment:
    if status_value not in ASSIGNMENT_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status. Allowed: {', '.join(sorted(ASSIGNMENT_STATUSES))}",
        )
    assignment.status = status_value
    assignment.end_date = end_date or _today()
    db.flush()
    return assignment


def ensure_assignment_for_year(
    db: Session,
    *,
    staff: User,
    academic_year_id: str,
    center_id: str,
    status_value: str = "active",
) -> StaffAssignment:
    existing = get_assignment_for_year(db, staff.id, academic_year_id)
    if existing:
        return existing
    return upsert_assignment(
        db,
        staff=staff,
        academic_year_id=academic_year_id,
        center_id=center_id,
        status_value=status_value,
    )


def primary_center_id_for_staff(db: Session, staff_id: str) -> str | None:
    row = (
        db.query(UserCenterAccess)
        .filter(UserCenterAccess.user_id == staff_id)
        .order_by(UserCenterAccess.created_at.asc(), UserCenterAccess.id.asc())
        .first()
    )
    return row.center_id if row else None


def backfill_staff_assignments_for_session(db: Session, institution_id: str | None = None) -> int:
    """Idempotent: create current-year assignments from UserCenterAccess primary center."""
    created = 0
    q = db.query(User)
    if institution_id:
        q = q.filter(User.institution_id == institution_id)
    staff_users = [u for u in q.all() if is_admin_account(u) or is_tutor_account(u)]

    by_institution: dict[str, list[User]] = {}
    for user in staff_users:
        by_institution.setdefault(user.institution_id, []).append(user)

    for inst_id, users in by_institution.items():
        year = get_current_academic_year(db, inst_id)
        if not year:
            continue
        for staff in users:
            if get_assignment_for_year(db, staff.id, year.id):
                continue
            center_id = primary_center_id_for_staff(db, staff.id)
            if not center_id:
                continue
            ensure_assignment_for_year(
                db,
                staff=staff,
                academic_year_id=year.id,
                center_id=center_id,
                status_value="active",
            )
            created += 1
    return created
