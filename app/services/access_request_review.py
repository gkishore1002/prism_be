"""Build rich context for reassignment request review."""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.assessment import Assessment, AssessmentSubmission
from app.models.csc import AssessmentAccessRequest
from app.models.user import StudentProfile
from app.services.analytics import get_recent_assessments
from app.services.institution_policies import get_institution_policies


def _parse_date(value: str | None) -> date | None:
    if not value or not value.strip():
        return None
    raw = value.strip()[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _format_date(d: date | None) -> str | None:
    if d is None:
        return None
    return d.strftime("%d %b %Y")


def _days_overdue(deadline: date | None) -> int | None:
    if deadline is None:
        return None
    delta = (date.today() - deadline).days
    return max(0, delta)


def _student_performance(db: Session, student_id: str) -> dict:
    recent = get_recent_assessments(db, student_id)
    accuracies = [r["accuracy"] for r in recent if r.get("accuracy") is not None]
    average_pct = round(sum(accuracies) / len(accuracies)) if accuracies else None

    submissions = (
        db.query(AssessmentSubmission)
        .filter(
            AssessmentSubmission.student_id == student_id,
            AssessmentSubmission.status.in_(("attended", "absent")),
        )
        .all()
    )
    previous_attempts = len(submissions)
    attended = sum(1 for s in submissions if s.status == "attended")
    countable = sum(1 for s in submissions if s.status in ("attended", "absent"))
    attendance_pct = round(100 * attended / countable) if countable else None

    return {
        "averagePct": average_pct,
        "previousAttempts": previous_attempts,
        "attendancePct": attendance_pct,
    }


def build_access_request_review_context(
    db: Session,
    request_id: str,
    institution_id: str,
) -> dict | None:
    req = db.get(AssessmentAccessRequest, request_id)
    if not req:
        return None
    assessment = db.get(Assessment, req.assessment_id)
    if not assessment or assessment.institution_id != institution_id:
        return None
    profile = db.get(StudentProfile, req.student_id)
    if not profile or profile.user.institution_id != institution_id:
        return None

    deadline_raw = assessment.available_until or assessment.scheduled_at
    deadline = _parse_date(deadline_raw)
    today = date.today()
    days_overdue = _days_overdue(deadline)

    assessment_attempts = (
        db.query(AssessmentSubmission)
        .filter(
            AssessmentSubmission.student_id == req.student_id,
            AssessmentSubmission.assessment_id == req.assessment_id,
        )
        .count()
    )

    all_requests = (
        db.query(AssessmentAccessRequest)
        .filter(AssessmentAccessRequest.student_id == req.student_id)
        .order_by(AssessmentAccessRequest.requested_at.desc())
        .all()
    )
    assessment_requests = [r for r in all_requests if r.assessment_id == req.assessment_id]
    prior_on_assessment = [r for r in assessment_requests if r.id != req.id]

    request_items = []
    for r in all_requests:
        a = db.get(Assessment, r.assessment_id)
        request_items.append(
            {
                "id": r.id,
                "assessmentId": r.assessment_id,
                "assessmentTitle": a.title if a else r.assessment_id,
                "status": r.status,
                "requestedAt": r.requested_at,
                "reviewedAt": r.reviewed_at,
            }
        )

    policies = get_institution_policies(db, institution_id)["assessment"]
    performance = _student_performance(db, req.student_id)

    return {
        "request": {
            "id": req.id,
            "status": req.status,
            "reason": req.reason or "",
            "requestedAt": req.requested_at,
            "reviewedAt": req.reviewed_at,
            "reviewNotes": req.review_notes,
            "accessGrantedUntil": req.access_granted_until,
        },
        "student": {
            "id": profile.id,
            "name": profile.user.name if profile.user else profile.id,
            "board": profile.board,
            "grade": profile.grade,
        },
        "assessment": {
            "id": assessment.id,
            "title": assessment.title,
            "subject": assessment.subject,
            "deadline": deadline_raw,
            "deadlineFormatted": _format_date(deadline),
            "today": today.isoformat(),
            "todayFormatted": _format_date(today),
            "daysOverdue": days_overdue,
        },
        "requestDetails": {
            "studentName": profile.user.name if profile.user else profile.id,
            "assessmentTitle": assessment.title,
            "deadline": deadline_raw,
            "deadlineFormatted": _format_date(deadline),
            "daysOverdue": days_overdue,
            "previousAttemptsOnAssessment": assessment_attempts,
            "reason": req.reason or "",
            "requestedOn": req.requested_at,
            "previousRequestsOnAssessment": len(prior_on_assessment),
        },
        "studentPerformance": performance,
        "previousRequests": {
            "total": len(all_requests),
            "approved": sum(1 for r in all_requests if r.status == "approved"),
            "rejected": sum(1 for r in all_requests if r.status == "rejected"),
            "pending": sum(1 for r in all_requests if r.status == "pending"),
            "items": request_items,
        },
        "policies": policies,
    }
