"""Student CSC and exam attendance tracking."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.assessment import Assessment, AssessmentSubmission
from app.models.csc import AssessmentAccessRequest, ReportCollectionLog
from app.models.user import StudentProfile, User
from app.services.csc_eligibility import days_until_csc_disable
from app.services.institution_policies import get_csc_policy


def latest_collections_for_students(
    db: Session, student_ids: list[str]
) -> dict[str, ReportCollectionLog]:
    if not student_ids:
        return {}
    rows = (
        db.query(ReportCollectionLog)
        .filter(ReportCollectionLog.student_id.in_(student_ids))
        .order_by(ReportCollectionLog.collected_at.desc())
        .all()
    )
    out: dict[str, ReportCollectionLog] = {}
    for row in rows:
        if row.student_id not in out:
            out[row.student_id] = row
    return out


def _collector_name(db: Session, user_id: str) -> str:
    user = db.get(User, user_id)
    return user.name if user else user_id


def collection_log_dict(db: Session, log: ReportCollectionLog) -> dict:
    return {
        "id": log.id,
        "studentId": log.student_id,
        "reportKind": log.report_kind,
        "reportRef": log.report_ref,
        "collectedAt": log.collected_at,
        "collectedByUserId": log.collected_by_user_id,
        "collectedByName": _collector_name(db, log.collected_by_user_id),
        "guardianName": log.guardian_name,
        "notes": log.notes,
    }


def exam_attendance_dict(submission: AssessmentSubmission, assessment: Assessment | None) -> dict:
    accuracy = 0
    if submission.max_score > 0:
        accuracy = round(100 * submission.score / submission.max_score)
    return {
        "assessmentId": submission.assessment_id,
        "assessmentTitle": assessment.title if assessment else submission.assessment_id,
        "subject": assessment.subject if assessment else "",
        "submittedAt": submission.submitted_at,
        "score": submission.score,
        "maxScore": submission.max_score,
        "accuracyPct": accuracy,
        "timeSpentMin": submission.time_spent_min,
        "status": submission.status,
    }


def access_request_dict(db: Session, req: AssessmentAccessRequest) -> dict:
    assessment = db.get(Assessment, req.assessment_id)
    reviewer_name = _collector_name(db, req.reviewed_by) if req.reviewed_by else None
    return {
        "id": req.id,
        "assessmentId": req.assessment_id,
        "assessmentTitle": assessment.title if assessment else req.assessment_id,
        "studentId": req.student_id,
        "reason": req.reason or "",
        "status": req.status,
        "requestedAt": req.requested_at,
        "reviewedBy": req.reviewed_by,
        "reviewedByName": reviewer_name,
        "reviewedAt": req.reviewed_at,
        "reviewNotes": req.review_notes,
        "accessGrantedUntil": req.access_granted_until,
    }


def student_tracking_payload(db: Session, profile: StudentProfile) -> dict:
    submissions = (
        db.query(AssessmentSubmission, Assessment)
        .join(Assessment, Assessment.id == AssessmentSubmission.assessment_id)
        .filter(AssessmentSubmission.student_id == profile.id)
        .order_by(AssessmentSubmission.submitted_at.desc())
        .all()
    )
    collections = (
        db.query(ReportCollectionLog)
        .filter(ReportCollectionLog.student_id == profile.id)
        .order_by(ReportCollectionLog.collected_at.desc())
        .all()
    )
    access_requests = (
        db.query(AssessmentAccessRequest)
        .filter(AssessmentAccessRequest.student_id == profile.id)
        .order_by(AssessmentAccessRequest.requested_at.desc())
        .all()
    )
    policy = get_csc_policy(db, profile.user.institution_id)
    latest = collections[0] if collections else None
    return {
        "studentId": profile.id,
        "studentName": profile.user.name if profile.user else profile.id,
        "lastCscInteractionAt": profile.last_csc_interaction_at,
        "daysUntilCscDisable": days_until_csc_disable(profile, policy),
        "lastCollectedByName": _collector_name(db, latest.collected_by_user_id) if latest else None,
        "lastCollectionGuardianName": latest.guardian_name if latest else None,
        "examAttendances": [
            exam_attendance_dict(sub, assessment) for sub, assessment in submissions
        ],
        "reportCollections": [collection_log_dict(db, log) for log in collections],
        "accessRequests": [access_request_dict(db, req) for req in access_requests],
    }
