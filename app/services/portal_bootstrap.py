"""Single-request portal bootstrap — replaces many parallel API calls on login."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.assessment import Assessment
from app.models.notification import Notification
from app.models.user import StudentProfile, User
from app.schemas import AssessmentOut, NotificationOut
from app.services import analytics as analytics_svc
from app.services.assessment_queries import list_assessments_for_student
from app.utils import from_json_list


def _resolve_student_id(db: Session, user: User) -> str:
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
    if not profile:
        profile = analytics_svc.ensure_student_profile(db, user)
    return profile.id


def _assessment_dict(a: Assessment, *, student_submitted: bool = False) -> dict:
    return AssessmentOut(
        id=a.id,
        title=a.title,
        board=a.board,
        grade=a.grade,
        subject=a.subject,
        scope=a.scope,  # type: ignore[arg-type]
        mode=a.mode,  # type: ignore[arg-type]
        batch_name=a.batch_name,
        question_count=a.question_count,
        duration_minutes=a.duration_minutes,
        scheduled_at=a.scheduled_at,
        status=a.status,  # type: ignore[arg-type]
        class_avg=a.class_avg,
        center_ids=from_json_list(a.center_ids),
        selected_question_ids=from_json_list(a.selected_question_ids),
        assigned_student_ids=from_json_list(a.assigned_student_ids),
        created_by_tutor_id=a.created_by_tutor_id,
        chapter=a.chapter,
        topic=a.topic,
        question_paper_id=a.question_paper_id,
        paper_coverage=a.paper_coverage,  # type: ignore[arg-type]
        selected_topics=from_json_list(a.selected_topics) if a.selected_topics else None,
        student_submitted=student_submitted,
    ).model_dump(by_alias=True)


def _notifications_for_role(db: Session, institution_id: str, role: str) -> list[dict]:
    notes = (
        db.query(Notification)
        .filter(Notification.institution_id == institution_id, Notification.role == role)
        .order_by(Notification.created_at.desc())
        .all()
    )
    return [
        NotificationOut(
            id=n.id,
            role=n.role,  # type: ignore[arg-type]
            kind=n.kind,  # type: ignore[arg-type]
            title=n.title,
            message=n.message,
            created_at=n.created_at,
            read=n.read,
            href=n.href,
        ).model_dump(by_alias=True)
        for n in notes
    ]


def _assessments_for_student(db: Session, institution_id: str, student_id: str) -> list[dict]:
    rows = list_assessments_for_student(db, institution_id, student_id)
    return [_assessment_dict(a, student_submitted=submitted) for a, submitted in rows]


def _all_assessments(db: Session, institution_id: str) -> list[dict]:
    rows = (
        db.query(Assessment)
        .filter(Assessment.institution_id == institution_id)
        .order_by(Assessment.scheduled_at.desc())
        .all()
    )
    return [_assessment_dict(a) for a in rows]


def student_bootstrap(db: Session, user: User) -> dict:
    sid = _resolve_student_id(db, user)
    inst_id = user.institution_id
    dash = analytics_svc.get_student_dashboard(db, sid, inst_id)
    return {
        **dash,
        "assessments": _assessments_for_student(db, inst_id, sid),
        "notifications": _notifications_for_role(db, inst_id, "student"),
    }


def tutor_bootstrap(db: Session, user: User) -> dict:
    from app.api.v1.curriculum import _batch_out, _build_curriculum_tree, _student_summary
    from app.models.content import Batch
    from app.models.user import StudentProfile as SP

    inst_id = user.institution_id
    dash = analytics_svc.get_tutor_dashboard(db, inst_id)
    students = db.query(SP).join(User).filter(User.institution_id == inst_id).all()
    batches = db.query(Batch).filter(Batch.institution_id == inst_id).all()
    return {
        **dash,
        "curriculum": [b.model_dump(by_alias=True) for b in _build_curriculum_tree(db, inst_id)],
        "batches": [_batch_out(db, b).model_dump(by_alias=True) for b in batches],
        "students": [_student_summary(s).model_dump(by_alias=True) for s in students],
        "assessments": _all_assessments(db, inst_id),
        "notifications": _notifications_for_role(db, inst_id, "tutor"),
    }


def admin_bootstrap(db: Session, user: User) -> dict:
    from app.api.v1.curriculum import _batch_out, _build_curriculum_tree, _student_summary
    from app.models.content import Batch
    from app.models.user import StudentProfile as SP

    inst_id = user.institution_id
    dash = analytics_svc.get_admin_dashboard(db, inst_id)
    students = db.query(SP).join(User).filter(User.institution_id == inst_id).all()
    batches = db.query(Batch).filter(Batch.institution_id == inst_id).all()
    return {
        **dash,
        "curriculum": [b.model_dump(by_alias=True) for b in _build_curriculum_tree(db, inst_id)],
        "batches": [_batch_out(db, b).model_dump(by_alias=True) for b in batches],
        "students": [_student_summary(s).model_dump(by_alias=True) for s in students],
        "assessments": _all_assessments(db, inst_id),
        "notifications": _notifications_for_role(db, inst_id, "admin"),
    }


def portal_bootstrap(db: Session, user: User, role: str) -> dict:
    if role == "student":
        payload = student_bootstrap(db, user)
    elif role == "tutor":
        payload = tutor_bootstrap(db, user)
    else:
        payload = admin_bootstrap(db, user)
    return {"role": role, **payload}
