"""Assessment deadline and access extension helpers."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.assessment import Assessment, AssessmentSubmission
from app.models.csc import AssessmentAccessRequest
from app.services.submissions import existing_submission


def _parse_date(value: str) -> date | None:
    if not value or not value.strip():
        return None
    raw = value.strip()[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _today() -> date:
    return date.today()


def get_active_access_request(
    db: Session, assessment_id: str, student_id: str
) -> AssessmentAccessRequest | None:
    return (
        db.query(AssessmentAccessRequest)
        .filter(
            AssessmentAccessRequest.assessment_id == assessment_id,
            AssessmentAccessRequest.student_id == student_id,
            AssessmentAccessRequest.status == "approved",
        )
        .order_by(AssessmentAccessRequest.reviewed_at.desc())
        .first()
    )


def has_approved_extension(db: Session, assessment_id: str, student_id: str) -> bool:
    req = get_active_access_request(db, assessment_id, student_id)
    if not req or not req.access_granted_until:
        return False
    until = _parse_date(req.access_granted_until)
    if until is None:
        return True
    return _today() <= until


def is_past_deadline(assessment: Assessment, student_id: str, db: Session) -> bool:
    """True when student missed the available-until window and has no approved extension."""
    if assessment.mode == "practice":
        return False
    if existing_submission(db, assessment.id, student_id):
        return False
    if has_approved_extension(db, assessment.id, student_id):
        return False
    deadline = _parse_date(assessment.available_until or assessment.scheduled_at)
    if deadline is None:
        return False
    return _today() > deadline


def get_access_request_status(
    db: Session, assessment_id: str, student_id: str
) -> str | None:
    req = (
        db.query(AssessmentAccessRequest)
        .filter(
            AssessmentAccessRequest.assessment_id == assessment_id,
            AssessmentAccessRequest.student_id == student_id,
        )
        .order_by(AssessmentAccessRequest.requested_at.desc())
        .first()
    )
    return req.status if req else None


def can_student_attend(
    db: Session,
    assessment: Assessment,
    student_id: str,
    *,
    has_submission: bool = False,
) -> bool:
    if has_submission:
        return False
    if assessment.mode == "practice":
        return assessment.status == "live"
    if has_approved_extension(db, assessment.id, student_id):
        return assessment.status in ("live", "completed")
    if is_past_deadline(assessment, student_id, db):
        return False
    return assessment.status == "live"


def mark_absent_for_pending_students(db: Session, assessment: Assessment) -> None:
    """When assessment is completed, mark invited students without submission as absent."""
    from app.utils import from_json_list

    invited = from_json_list(assessment.assigned_student_ids)
    existing = {
        s.student_id
        for s in db.query(AssessmentSubmission)
        .filter(AssessmentSubmission.assessment_id == assessment.id)
        .all()
    }
    now = datetime.now().isoformat(timespec="minutes")
    for sid in invited:
        if sid in existing:
            continue
        import uuid

        db.add(
            AssessmentSubmission(
                id=f"sub-{uuid.uuid4().hex[:8]}",
                assessment_id=assessment.id,
                student_id=sid,
                score=0,
                max_score=0,
                time_spent_min=0,
                submitted_at=now,
                status="absent",
                answers="[]",
            )
        )


def default_access_granted_until(days: int = 3) -> str:
    return (_today() + timedelta(days=days)).isoformat()
