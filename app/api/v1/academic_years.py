"""Academic year CRUD and student enrollment / promote endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, get_effective_role, get_token_payload, require_roles
from app.core.routing import CamelCaseAPIRoute
from app.models.user import StudentProfile, User
from app.schemas import (
    AcademicYearCreate,
    AcademicYearOut,
    AcademicYearSetCurrent,
    StudentEnrollmentOut,
    StudentPromoteIn,
)
from app.services import enrollments as enr_svc
from app.services.branch_access import assert_can_access_student

router = APIRouter(tags=["academic-years", "enrollments"], route_class=CamelCaseAPIRoute)


def _year_out(year) -> AcademicYearOut:
    return AcademicYearOut(**enr_svc.academic_year_to_dict(year))


def _enrollment_out(db: Session, enrollment) -> StudentEnrollmentOut:
    return StudentEnrollmentOut(**enr_svc.enrollment_to_dict(db, enrollment))


@router.get("/academic-years", response_model=list[AcademicYearOut])
def list_years(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> list[AcademicYearOut]:
    years = enr_svc.list_academic_years(db, user.institution_id)
    # Only staff may bootstrap a missing year so students cannot mutate org config.
    role = get_effective_role(payload, user)
    if not years and role in ("admin", "tutor"):
        year = enr_svc.ensure_academic_year(
            db, user.institution_id, "2025-26", make_current=True
        )
        db.commit()
        db.refresh(year)
        years = [year]
    return [_year_out(y) for y in years]


@router.post("/academic-years", response_model=AcademicYearOut, status_code=status.HTTP_201_CREATED)
def create_year(
    body: AcademicYearCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
) -> AcademicYearOut:
    year = enr_svc.create_academic_year(
        db,
        user.institution_id,
        name=body.name,
        start_date=body.start_date,
        end_date=body.end_date,
        is_current=body.is_current,
    )
    return _year_out(year)


@router.patch("/academic-years/{year_id}", response_model=AcademicYearOut)
def patch_year(
    year_id: str,
    body: AcademicYearSetCurrent,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
) -> AcademicYearOut:
    if body.is_current:
        year = enr_svc.set_current_academic_year(db, user.institution_id, year_id)
        return _year_out(year)
    year = enr_svc.get_academic_year(db, user.institution_id, year_id=year_id)
    if not year:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Academic year not found")
    return _year_out(year)


@router.get("/students/{student_id}/enrollments", response_model=list[StudentEnrollmentOut])
def list_student_enrollments(
    student_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> list[StudentEnrollmentOut]:
    profile = db.get(StudentProfile, student_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    role = get_effective_role(payload, user)
    assert_can_access_student(db, user, role, profile)
    return [_enrollment_out(db, e) for e in enr_svc.list_enrollments_for_student(db, student_id)]


@router.get("/enrollments/{enrollment_id}", response_model=StudentEnrollmentOut)
def get_enrollment(
    enrollment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> StudentEnrollmentOut:
    enrollment = enr_svc.get_enrollment(db, enrollment_id)
    if not enrollment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enrollment not found")
    profile = db.get(StudentProfile, enrollment.student_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    role = get_effective_role(payload, user)
    assert_can_access_student(db, user, role, profile)
    return _enrollment_out(db, enrollment)


@router.post(
    "/students/{student_id}/promote",
    response_model=StudentEnrollmentOut,
    status_code=status.HTTP_201_CREATED,
)
def promote_student(
    student_id: str,
    body: StudentPromoteIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "tutor")),
    payload: dict = Depends(get_token_payload),
) -> StudentEnrollmentOut:
    profile = db.get(StudentProfile, student_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    role = get_effective_role(payload, user)
    assert_can_access_student(db, user, role, profile)
    enrollment = enr_svc.promote_student(
        db,
        profile=profile,
        academic_year_id=body.academic_year_id,
        board=body.board,
        grade=body.grade,
        batch_id=body.batch_id,
        center_id=body.center_id,
        prior_status=body.prior_status,
    )
    from app.services.centers import sync_center_counts

    sync_center_counts(db, user.institution_id, commit=True)
    return _enrollment_out(db, enrollment)


@router.get("/me/enrollments", response_model=list[StudentEnrollmentOut])
def my_enrollments(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("student")),
) -> list[StudentEnrollmentOut]:
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student profile not found")
    return [_enrollment_out(db, e) for e in enr_svc.list_enrollments_for_student(db, profile.id)]
