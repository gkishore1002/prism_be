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
    academic_year_id: str | None = None,
    academic_year: str | None = None,
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
    if academic_year_id or academic_year:
        from app.models.enrollment import AcademicYear, StudentEnrollment

        q = q.join(
            StudentEnrollment, StudentEnrollment.student_id == StudentProfile.id
        )
        if academic_year_id:
            q = q.filter(StudentEnrollment.academic_year_id == academic_year_id)
        elif academic_year and institution_id:
            q = q.join(AcademicYear, AcademicYear.id == StudentEnrollment.academic_year_id).filter(
                AcademicYear.institution_id == institution_id,
                AcademicYear.name == academic_year.strip(),
            )
        # Board/grade from enrollment for that year; center stays on profile so
        # existing branch_access / apply_branch_scope_to_students remains authoritative.
        if board:
            q = q.filter(StudentEnrollment.board.ilike(board))
        if grade:
            q = q.filter(StudentEnrollment.grade == grade)
        if center:
            q = q.filter(StudentProfile.center_id == center)
    else:
        if center:
            q = q.filter(StudentProfile.center_id == center)
        if board:
            q = q.filter(StudentProfile.board.ilike(board))
        if grade:
            q = q.filter(StudentProfile.grade == grade)
    if status:
        q = q.filter(StudentProfile.status == status)
    if batch and institution_id and db:
        batch_q = db.query(Batch).filter(Batch.institution_id == institution_id, Batch.name == batch)
        if academic_year_id:
            batch_q = batch_q.filter(Batch.academic_year_id == academic_year_id)
        batch_row = batch_q.first()
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
    academic_year_id: str | None = None,
    academic_year: str | None = None,
) -> dict:
    q = student_master_base_query(db, institution_id)
    q = apply_student_master_filters(
        q,
        center=center,
        academic_year_id=academic_year_id,
        academic_year=academic_year,
        institution_id=institution_id,
        db=db,
    )
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
        "currentEnrollmentId": profile.current_enrollment_id,
        "schoolName": profile.school_name,
        "email": profile.user.email,
        "status": profile.status,
        "lastCscInteractionAt": profile.last_csc_interaction_at,
        "disableReason": profile.disable_reason,
        "daysUntilCscDisable": days_until_csc_disable(profile, db=db),
    }
