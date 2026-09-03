from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, get_effective_role, get_token_payload, require_roles
from app.core.routing import CamelCaseAPIRoute
from app.models.user import StudentProfile, User
from app.services import analytics as svc

router = APIRouter(prefix="/analytics", tags=["analytics"], route_class=CamelCaseAPIRoute)


def _resolve_scope(
    db: Session,
    user: User,
    payload: dict,
    center_id: str | None,
) -> list[str] | None:
    from app.services.branch_access import resolve_branch_filter

    role = get_effective_role(payload, user)
    return resolve_branch_filter(db, user, role, center_id)


def _resolve_student(db: Session, user: User, student_id: str | None, payload: dict) -> str:
    if student_id:
        profile = db.get(StudentProfile, student_id)
        if not profile or profile.user.institution_id != user.institution_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
        return student_id
    role = get_effective_role(payload, user)
    if role == "student":
        profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
        if not profile:
            profile = svc.ensure_student_profile(db, user)
        return profile.id
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="student_id required")


def resolve_student_id(
    student_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> str:
    return _resolve_student(db, user, student_id, payload)


# ─── Institution / Admin ─────────────────────────────────────────────────────

@router.get("/institution/overview")
def institution_overview(
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> dict:
    scope = _resolve_scope(db, user, payload, center_id)
    return svc.get_institution_overview(db, user.institution_id, center_ids=scope)


@router.get("/institution/operational-stats")
def institution_operational_stats(
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "tutor")),
    payload: dict = Depends(get_token_payload),
) -> dict:
    scope = _resolve_scope(db, user, payload, center_id)
    return svc.get_institution_operational_stats(db, user.institution_id, center_ids=scope)


@router.get("/institution/centers")
def institution_centers(
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> list:
    scope = _resolve_scope(db, user, payload, center_id)
    return svc.get_centers_analytics(db, user.institution_id, center_ids=scope)


@router.get("/institution/boards")
def institution_boards(
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> list:
    scope = _resolve_scope(db, user, payload, center_id)
    return svc.get_board_report(db, user.institution_id, center_ids=scope)


@router.get("/institution/teachers")
def institution_teachers(
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> list:
    scope = _resolve_scope(db, user, payload, center_id)
    return svc.get_teachers(db, user.institution_id, center_ids=scope)


@router.get("/institution/hardest-topics")
def institution_hardest_topics(
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> list:
    scope = _resolve_scope(db, user, payload, center_id)
    return svc.get_hardest_topics(db, user.institution_id, center_ids=scope)


@router.get("/institution/syllabus")
def institution_syllabus(
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> list:
    scope = _resolve_scope(db, user, payload, center_id)
    return svc.get_syllabus_completion(db, user.institution_id, center_ids=scope)


@router.get("/institution/monthly-trend")
def institution_monthly_trend(
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> list:
    scope = _resolve_scope(db, user, payload, center_id)
    return svc.get_monthly_trend(db, user.institution_id, center_ids=scope)


@router.get("/institution/subject-health")
def institution_subject_health(
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> list:
    scope = _resolve_scope(db, user, payload, center_id)
    return svc.get_subject_health_distribution(db, user.institution_id, center_ids=scope)


@router.get("/students/master")
def student_master_profiles(
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "tutor")),
    payload: dict = Depends(get_token_payload),
) -> list:
    scope = _resolve_scope(db, user, payload, center_id)
    return svc.get_student_master_profiles(db, user.institution_id, center_ids=scope)


@router.get("/users/tutor-names")
def tutor_names(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    return svc.get_tutor_name_map(db, user.institution_id)


# ─── Student ─────────────────────────────────────────────────────────────────

@router.get("/student/profile")
def student_profile(
    sid: str = Depends(resolve_student_id),
    db: Session = Depends(get_db),
) -> dict:
    data = svc.get_student_profile(db, sid)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    return data


@router.get("/student/health")
def student_health(sid: str = Depends(resolve_student_id), db: Session = Depends(get_db)) -> dict:
    return svc.get_student_health(db, sid)


@router.get("/student/gaps")
def student_gaps(sid: str = Depends(resolve_student_id), db: Session = Depends(get_db)) -> list:
    return svc.get_learning_gaps(db, sid)


@router.get("/student/recovery")
def student_recovery(sid: str = Depends(resolve_student_id), db: Session = Depends(get_db)) -> list:
    return svc.get_recovery_plan(db, sid)


@router.get("/student/readiness")
def student_readiness(sid: str = Depends(resolve_student_id), db: Session = Depends(get_db)) -> list:
    return svc.get_readiness_predictions(db, sid)


@router.get("/student/improvement-trend")
def student_improvement_trend(sid: str = Depends(resolve_student_id), db: Session = Depends(get_db)) -> list:
    return svc.get_improvement_trend(db, sid)


@router.get("/student/topic-breakdown")
def student_topic_breakdown(sid: str = Depends(resolve_student_id), db: Session = Depends(get_db)) -> list:
    return svc.get_topic_breakdown(db, sid)


@router.get("/student/topic-readiness")
def student_topic_readiness(sid: str = Depends(resolve_student_id), db: Session = Depends(get_db)) -> list:
    return svc.get_topic_readiness(db, sid)


@router.get("/student/subjects")
def student_subjects(sid: str = Depends(resolve_student_id), db: Session = Depends(get_db)) -> list:
    return svc.get_student_subjects(db, sid)


@router.get("/student/recent-assessments")
def student_recent_assessments(sid: str = Depends(resolve_student_id), db: Session = Depends(get_db)) -> list:
    return svc.get_recent_assessments(db, sid)


@router.get("/student/report")
def student_report(sid: str = Depends(resolve_student_id), db: Session = Depends(get_db)) -> dict:
    data = svc.get_student_wise_report(db, sid)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return data


@router.get("/student/overall-report")
def student_overall_report(sid: str = Depends(resolve_student_id), db: Session = Depends(get_db)) -> dict:
    data = svc.get_overall_performance_report(db, sid)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return data


@router.get("/student/assessment-reports")
def student_assessment_reports(sid: str = Depends(resolve_student_id), db: Session = Depends(get_db)) -> list:
    from app.services.assessment_report import list_assessment_reports

    return list_assessment_reports(db, sid)


@router.get("/student/assessment-reports/{assessment_id}")
def student_assessment_report(
    assessment_id: str,
    sid: str = Depends(resolve_student_id),
    db: Session = Depends(get_db),
) -> dict:
    from app.services.assessment_report import get_assessment_report

    data = get_assessment_report(db, assessment_id, sid)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment report not found")
    return data


@router.get("/student/assessment-reports/{assessment_id}/summary")
def student_assessment_report_summary(
    assessment_id: str,
    sid: str = Depends(resolve_student_id),
    db: Session = Depends(get_db),
) -> dict:
    from app.services.assessment_report import get_assessment_report_summary

    data = get_assessment_report_summary(db, assessment_id, sid)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment report not found")
    return data


@router.get("/student/monthly-reports")
def student_monthly_reports(sid: str = Depends(resolve_student_id), db: Session = Depends(get_db)) -> list:
    return svc.get_monthly_reports(db, sid)


@router.get("/student/alerts")
def student_alerts(sid: str = Depends(resolve_student_id), db: Session = Depends(get_db)) -> list:
    return svc.get_progress_alerts(db, sid)


# ─── Tutor ───────────────────────────────────────────────────────────────────

@router.get("/tutor/topic-weakness")
def tutor_topic_weakness(
    batch_name: str | None = Query(None),
    batch_id: str | None = Query(None),
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
    payload: dict = Depends(get_token_payload),
) -> list:
    scope = _resolve_scope(db, user, payload, center_id)
    return svc.get_tutor_topic_weakness(
        db, user.institution_id, batch_name, batch_id=batch_id, center_ids=scope
    )


@router.get("/tutor/at-risk")
def tutor_at_risk(
    batch_name: str | None = Query(None),
    batch_id: str | None = Query(None),
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
    payload: dict = Depends(get_token_payload),
) -> list:
    scope = _resolve_scope(db, user, payload, center_id)
    return svc.get_tutor_at_risk(
        db, user.institution_id, batch_id=batch_id, batch_name=batch_name, center_ids=scope
    )


@router.get("/tutor/batch-heatmap")
def tutor_batch_heatmap(
    batch_name: str | None = Query(None),
    batch_id: str | None = Query(None),
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
    payload: dict = Depends(get_token_payload),
) -> list:
    scope = _resolve_scope(db, user, payload, center_id)
    return svc.get_tutor_batch_heatmap(
        db, user.institution_id, batch_id=batch_id, batch_name=batch_name, center_ids=scope
    )


@router.get("/tutor/cohort-report")
def tutor_cohort_report(
    batch_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> dict:
    from app.services import cohort_report as cohort_svc

    return cohort_svc.get_cohort_report(db, user.institution_id, batch_id)


@router.get("/student/genome")
def student_genome(
    sid: str = Depends(resolve_student_id),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin", "student")),
) -> dict:
    from app.services import cohort_report as cohort_svc

    data = cohort_svc.get_student_genome(db, user.institution_id, sid)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    return data


@router.get("/tutor/class-insights")
def tutor_class_insights(
    center_id: str | None = Query(None),
    batch_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
    payload: dict = Depends(get_token_payload),
) -> list:
    scope = _resolve_scope(db, user, payload, center_id)
    return svc.get_class_insights(
        db, user.institution_id, center_ids=scope, batch_id=batch_id
    )


@router.get("/tutor/copilot")
def tutor_copilot(
    batch_name: str | None = Query(None),
    batch_id: str | None = Query(None),
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
    payload: dict = Depends(get_token_payload),
) -> dict:
    scope = _resolve_scope(db, user, payload, center_id)
    return svc.get_tutor_copilot_summary(
        db, user.institution_id, batch_name, batch_id=batch_id, center_ids=scope
    )


@router.get("/subjects/{subject}/topics")
def subject_topics(subject: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list:
    return svc.get_subject_topics(db, user.institution_id, subject)


@router.get("/subjects/{subject}/students")
def subject_students(subject: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list:
    return svc.get_subject_students(db, user.institution_id, subject)
