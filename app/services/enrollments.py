"""Academic year + student enrollment services."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.content import Batch, BatchStudent
from app.models.enrollment import AcademicYear, StudentEnrollment
from app.models.user import StudentProfile

ENROLLMENT_STATUSES = frozenset(
    {"active", "completed", "detained", "transferred", "dropped", "graduated", "inactive"}
)
PRIOR_STATUSES = frozenset({"completed", "detained", "transferred", "dropped", "graduated", "inactive"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def list_academic_years(db: Session, institution_id: str) -> list[AcademicYear]:
    return (
        db.query(AcademicYear)
        .filter(AcademicYear.institution_id == institution_id)
        .order_by(AcademicYear.name.desc())
        .all()
    )


def get_academic_year(
    db: Session,
    institution_id: str,
    *,
    year_id: str | None = None,
    name: str | None = None,
) -> AcademicYear | None:
    q = db.query(AcademicYear).filter(AcademicYear.institution_id == institution_id)
    if year_id:
        return q.filter(AcademicYear.id == year_id).first()
    if name:
        return q.filter(AcademicYear.name == name).first()
    return None


def get_current_academic_year(db: Session, institution_id: str) -> AcademicYear | None:
    year = (
        db.query(AcademicYear)
        .filter(AcademicYear.institution_id == institution_id, AcademicYear.is_current.is_(True))
        .first()
    )
    if year:
        return year
    return (
        db.query(AcademicYear)
        .filter(AcademicYear.institution_id == institution_id)
        .order_by(AcademicYear.name.desc())
        .first()
    )


def resolve_academic_year(
    db: Session,
    institution_id: str,
    *,
    academic_year_id: str | None = None,
    academic_year: str | None = None,
    required: bool = False,
    default_to_current: bool = True,
) -> AcademicYear | None:
    """Resolve an academic year.

    When neither id nor name is provided:
    - default_to_current=True (create/promote): return the institution's current year
    - default_to_current=False (list/analytics): return None so callers keep prior
      all-students / all-batches behavior unless the client explicitly scopes by year
    """
    if academic_year_id:
        year = get_academic_year(db, institution_id, year_id=academic_year_id)
        if year:
            return year
        if required:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Academic year not found")
        return None
    if academic_year and academic_year.strip():
        year = get_academic_year(db, institution_id, name=academic_year.strip())
        if year:
            return year
        if required:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Academic year not found")
        return None
    if not default_to_current:
        if required:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Academic year is required",
            )
        return None
    year = get_current_academic_year(db, institution_id)
    if required and not year:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No academic year configured for this institution",
        )
    return year


def ensure_academic_year(
    db: Session,
    institution_id: str,
    name: str,
    *,
    start_date: str = "",
    end_date: str = "",
    make_current: bool = False,
) -> AcademicYear:
    name = (name or "").strip() or "2025-26"
    existing = get_academic_year(db, institution_id, name=name)
    if existing:
        if make_current and not existing.is_current:
            set_current_academic_year(db, institution_id, existing.id, commit=False)
            db.refresh(existing)
        return existing
    if make_current:
        db.query(AcademicYear).filter(
            AcademicYear.institution_id == institution_id,
            AcademicYear.is_current.is_(True),
        ).update({"is_current": False})
    year = AcademicYear(
        id=_new_id("ay"),
        institution_id=institution_id,
        name=name,
        start_date=start_date or "",
        end_date=end_date or "",
        is_current=make_current or not list_academic_years(db, institution_id),
    )
    # If this is the first year, force current
    if not db.query(AcademicYear).filter(AcademicYear.institution_id == institution_id).count():
        year.is_current = True
    db.add(year)
    db.flush()
    return year


def create_academic_year(
    db: Session,
    institution_id: str,
    *,
    name: str,
    start_date: str = "",
    end_date: str = "",
    is_current: bool = False,
) -> AcademicYear:
    if get_academic_year(db, institution_id, name=name.strip()):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Academic year already exists")
    year = ensure_academic_year(
        db,
        institution_id,
        name,
        start_date=start_date,
        end_date=end_date,
        make_current=is_current,
    )
    db.commit()
    db.refresh(year)
    return year


def set_current_academic_year(
    db: Session,
    institution_id: str,
    year_id: str,
    *,
    commit: bool = True,
) -> AcademicYear:
    year = get_academic_year(db, institution_id, year_id=year_id)
    if not year:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Academic year not found")
    db.query(AcademicYear).filter(
        AcademicYear.institution_id == institution_id,
        AcademicYear.is_current.is_(True),
    ).update({"is_current": False})
    year.is_current = True
    if commit:
        db.commit()
        db.refresh(year)
    else:
        db.flush()
    return year


def academic_year_to_dict(year: AcademicYear) -> dict:
    return {
        "id": year.id,
        "institutionId": year.institution_id,
        "name": year.name,
        "startDate": year.start_date or "",
        "endDate": year.end_date or "",
        "isCurrent": bool(year.is_current),
    }


def batch_display_name(db: Session, batch_id: str | None) -> str:
    if not batch_id:
        return ""
    batch = db.get(Batch, batch_id)
    return batch.name if batch else ""


def enrollment_to_dict(db: Session, enrollment: StudentEnrollment) -> dict:
    year = db.get(AcademicYear, enrollment.academic_year_id)
    center_name = ""
    if enrollment.center_id:
        from app.models.institution import Center

        center = db.get(Center, enrollment.center_id)
        center_name = center.name if center else ""
    return {
        "id": enrollment.id,
        "studentId": enrollment.student_id,
        "academicYearId": enrollment.academic_year_id,
        "academicYear": year.name if year else "",
        "board": enrollment.board,
        "grade": enrollment.grade,
        "batchId": enrollment.batch_id,
        "batch": batch_display_name(db, enrollment.batch_id),
        "centerId": enrollment.center_id,
        "centerName": center_name,
        "status": enrollment.status,
        "enrolledAt": enrollment.enrolled_at or "",
        "completedAt": enrollment.completed_at,
        "isCurrent": bool(year.is_current) if year else False,
    }


def sync_profile_from_enrollment(
    db: Session,
    profile: StudentProfile,
    enrollment: StudentEnrollment,
) -> None:
    year = db.get(AcademicYear, enrollment.academic_year_id)
    profile.board = enrollment.board
    profile.grade = enrollment.grade
    profile.center_id = enrollment.center_id
    profile.academic_year = year.name if year else profile.academic_year
    profile.batch = batch_display_name(db, enrollment.batch_id) or profile.batch
    profile.current_enrollment_id = enrollment.id
    if enrollment.status == "active":
        profile.status = "active"
    elif enrollment.status in {"inactive", "dropped", "transferred", "graduated"}:
        profile.status = "inactive"


def get_enrollment(db: Session, enrollment_id: str) -> StudentEnrollment | None:
    return db.get(StudentEnrollment, enrollment_id)


def list_enrollments_for_student(db: Session, student_id: str) -> list[StudentEnrollment]:
    return (
        db.query(StudentEnrollment)
        .filter(StudentEnrollment.student_id == student_id)
        .order_by(StudentEnrollment.enrolled_at.desc(), StudentEnrollment.id.desc())
        .all()
    )


def get_enrollment_for_year(
    db: Session,
    student_id: str,
    academic_year_id: str,
) -> StudentEnrollment | None:
    return (
        db.query(StudentEnrollment)
        .filter(
            StudentEnrollment.student_id == student_id,
            StudentEnrollment.academic_year_id == academic_year_id,
        )
        .first()
    )


def get_active_enrollment(
    db: Session,
    profile: StudentProfile,
    *,
    academic_year_id: str | None = None,
) -> StudentEnrollment | None:
    if academic_year_id:
        return get_enrollment_for_year(db, profile.id, academic_year_id)
    if profile.current_enrollment_id:
        enr = db.get(StudentEnrollment, profile.current_enrollment_id)
        if enr:
            return enr
    institution_id = profile.user.institution_id
    current = get_current_academic_year(db, institution_id)
    if current:
        enr = get_enrollment_for_year(db, profile.id, current.id)
        if enr:
            return enr
    enrollments = list_enrollments_for_student(db, profile.id)
    return enrollments[0] if enrollments else None


def create_enrollment(
    db: Session,
    *,
    student_id: str,
    academic_year_id: str,
    board: str,
    grade: str,
    batch_id: str | None = None,
    center_id: str | None = None,
    enrollment_status: str = "active",
    set_as_current: bool = True,
) -> StudentEnrollment:
    if enrollment_status not in ENROLLMENT_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid enrollment status")
    existing = get_enrollment_for_year(db, student_id, academic_year_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Student already has an enrollment for this academic year",
        )
    enrollment = StudentEnrollment(
        id=_new_id("enr"),
        student_id=student_id,
        academic_year_id=academic_year_id,
        board=board,
        grade=grade,
        batch_id=batch_id,
        center_id=center_id,
        status=enrollment_status,
        enrolled_at=_now_iso(),
    )
    db.add(enrollment)
    db.flush()
    if batch_id:
        link = (
            db.query(BatchStudent)
            .filter(BatchStudent.batch_id == batch_id, BatchStudent.student_id == student_id)
            .first()
        )
        if not link:
            db.add(BatchStudent(batch_id=batch_id, student_id=student_id))
    profile = db.get(StudentProfile, student_id)
    if profile and set_as_current:
        sync_profile_from_enrollment(db, profile, enrollment)
    return enrollment


def promote_student(
    db: Session,
    *,
    profile: StudentProfile,
    academic_year_id: str,
    board: str,
    grade: str,
    batch_id: str | None,
    center_id: str | None,
    prior_status: str = "completed",
) -> StudentEnrollment:
    if prior_status not in PRIOR_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid prior status")
    year = db.get(AcademicYear, academic_year_id)
    if not year or year.institution_id != profile.user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Academic year not found")
    if get_enrollment_for_year(db, profile.id, academic_year_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Student already enrolled in the target academic year",
        )

    current = get_active_enrollment(db, profile)
    if current and current.academic_year_id == academic_year_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot promote into the same academic year",
        )
    if current and current.status == "active":
        current.status = prior_status
        current.completed_at = _now_iso()

    enrollment = create_enrollment(
        db,
        student_id=profile.id,
        academic_year_id=academic_year_id,
        board=board,
        grade=grade,
        batch_id=batch_id,
        center_id=center_id or (current.center_id if current else profile.center_id),
        enrollment_status="active",
        set_as_current=True,
    )
    db.commit()
    db.refresh(enrollment)
    return enrollment


def student_ids_for_academic_year(
    db: Session,
    institution_id: str,
    academic_year_id: str,
    *,
    center_ids: list[str] | None = None,
) -> set[str]:
    from app.models.user import User

    q = (
        db.query(StudentEnrollment.student_id)
        .join(StudentProfile, StudentProfile.id == StudentEnrollment.student_id)
        .join(User, User.id == StudentProfile.user_id)
        .filter(
            User.institution_id == institution_id,
            StudentEnrollment.academic_year_id == academic_year_id,
        )
    )
    if center_ids is not None:
        q = q.filter(StudentEnrollment.center_id.in_(center_ids))
    return {row[0] for row in q.all()}
