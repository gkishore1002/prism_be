"""Paginated student master list queries for admin/tutor management."""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from app.models.content import Batch, BatchStudent
from app.models.user import StudentProfile, User
from app.services.csc_eligibility import days_until_csc_disable


def student_master_base_query(db: Session, institution_id: str) -> Query:
    return (
        db.query(StudentProfile)
        .join(User)
        .filter(User.institution_id == institution_id)
    )


def apply_student_master_filters(
    q: Query,
    *,
    search: str | None = None,
    center: str | None = None,
    status: str | None = None,
    board: str | None = None,
    grade: str | None = None,
    batch: str | None = None,
    institution_id: str | None = None,
    db: Session | None = None,
) -> Query:
    if search and search.strip():
        term = f"%{search.strip()}%"
        q = q.filter(
            or_(
                User.name.ilike(term),
                StudentProfile.batch.ilike(term),
                User.email.ilike(term),
                StudentProfile.school_name.ilike(term),
            )
        )
    if center:
        q = q.filter(StudentProfile.center_id == center)
    if status:
        q = q.filter(StudentProfile.status == status)
    if board:
        q = q.filter(StudentProfile.board.ilike(board))
    if grade:
        q = q.filter(StudentProfile.grade == grade)
    if batch and institution_id and db:
        batch_row = (
            db.query(Batch)
            .filter(Batch.institution_id == institution_id, Batch.name == batch)
            .first()
        )
        if batch_row:
            q = q.join(BatchStudent, BatchStudent.student_id == StudentProfile.id).filter(
                BatchStudent.batch_id == batch_row.id
            )
        else:
            q = q.filter(StudentProfile.batch == batch)
    return q.order_by(User.name.asc())


def student_master_stats(
    db: Session,
    institution_id: str,
    *,
    center: str | None = None,
) -> dict:
    q = student_master_base_query(db, institution_id)
    if center:
        q = q.filter(StudentProfile.center_id == center)
    total = q.count()
    active = q.filter(StudentProfile.status == "active").count()
    return {"total": total, "active": active, "inactive": total - active}


def student_profile_to_master_dict(db: Session, profile: StudentProfile) -> dict:
    batch_ids = [
        row.batch_id
        for row in db.query(BatchStudent).filter(BatchStudent.student_id == profile.id).all()
    ]
    return {
        "id": profile.id,
        "name": profile.user.name,
        "board": profile.board,
        "grade": profile.grade,
        "batch": profile.batch,
        "batchIds": batch_ids,
        "centerId": profile.center_id,
        "academicYear": profile.academic_year,
        "schoolName": profile.school_name,
        "email": profile.user.email,
        "status": profile.status,
        "lastCscInteractionAt": profile.last_csc_interaction_at,
        "disableReason": profile.disable_reason,
        "daysUntilCscDisable": days_until_csc_disable(profile, db=db),
    }
