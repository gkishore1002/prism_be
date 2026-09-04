"""Exam session lock, heartbeat, and proctoring violations."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.academic import Question
from app.models.assessment import Assessment, AssessmentSubmission, ExamSession, ExamViolation
from app.models.user import StudentProfile
from app.services.assessment_report import build_and_store_assessment_report
from app.services.submissions import (
    existing_attempt,
    existing_submission,
    update_assessment_class_avg,
    update_student_profile_after_submission,
)
from app.utils import dict_get, from_json_list

MAX_PROCTOR_VIOLATIONS = 3
TERMINATION_REASON = "EXCESSIVE_PROCTORING_VIOLATIONS"
ALLOWED_VIOLATION_TYPES = frozenset(
    {"FULLSCREEN_EXIT", "TAB_SWITCH", "WINDOW_BLUR", "DEVTOOLS_ATTEMPT"}
)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def violation_count(db: Session, assessment_id: str, student_id: str) -> int:
    return (
        db.query(ExamViolation)
        .filter(
            ExamViolation.assessment_id == assessment_id,
            ExamViolation.student_id == student_id,
        )
        .count()
    )


def active_session(
    db: Session, assessment_id: str, student_id: str
) -> ExamSession | None:
    return (
        db.query(ExamSession)
        .filter(
            ExamSession.assessment_id == assessment_id,
            ExamSession.student_id == student_id,
            ExamSession.status == "active",
        )
        .first()
    )


def assert_active_device_session(
    db: Session,
    assessment: Assessment,
    student_id: str,
    device_id: str | None,
) -> ExamSession | None:
    """Require matching active session for assessment mode. Practice skips."""
    if assessment.mode == "practice":
        return None
    if not device_id or not device_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device id required for this exam session.",
        )
    session = active_session(db, assessment.id, student_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No active exam session. Restart the exam from your assessments list.",
        )
    if session.device_id != device_id.strip():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Exam already active on another device.",
        )
    return session


def claim_session(
    db: Session,
    *,
    assessment: Assessment,
    student_id: str,
    device_id: str,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[ExamSession, int]:
    device = device_id.strip()
    if not device:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="deviceId is required")
    if existing_submission(db, assessment.id, student_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assessment already submitted.",
        )

    now = _now_iso()
    session = active_session(db, assessment.id, student_id)
    if session is not None:
        if session.device_id != device:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Exam already active on another device.",
            )
        session.last_heartbeat_at = now
        if user_agent:
            session.user_agent = user_agent
        if ip_address:
            session.ip_address = ip_address
        db.add(session)
        db.commit()
        db.refresh(session)
        return session, violation_count(db, assessment.id, student_id)

    session = ExamSession(
        id=f"es-{uuid.uuid4().hex[:10]}",
        assessment_id=assessment.id,
        student_id=student_id,
        device_id=device,
        status="active",
        started_at=now,
        last_heartbeat_at=now,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session, violation_count(db, assessment.id, student_id)


def heartbeat_for_assessment(
    db: Session,
    *,
    assessment: Assessment,
    student_id: str,
    device_id: str,
) -> ExamSession:
    session = assert_active_device_session(db, assessment, student_id, device_id)
    assert session is not None
    session.last_heartbeat_at = _now_iso()
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def end_session(
    db: Session,
    *,
    assessment_id: str,
    student_id: str,
    status_value: str = "ended",
) -> None:
    session = active_session(db, assessment_id, student_id)
    if session is None:
        return
    session.status = status_value
    session.ended_at = _now_iso()
    db.add(session)


def _score_answers(
    db: Session, assessment: Assessment, answers: list[dict]
) -> tuple[int, int]:
    qids = from_json_list(assessment.selected_question_ids)
    questions = {q.id: q for q in db.query(Question).filter(Question.id.in_(qids)).all()} if qids else {}
    score = 0
    max_score = sum(q.marks for q in questions.values())
    for ans in answers:
        qid = dict_get(ans, "question_id", "questionId")
        selected = dict_get(ans, "selected_option", "selectedOption", default="")
        q = questions.get(qid)
        if q and q.correct_answer and selected and str(selected).upper() == q.correct_answer.upper():
            score += q.marks
    return score, max_score


def finalize_attempt_as_attended(
    db: Session,
    *,
    assessment: Assessment,
    profile: StudentProfile,
    answers: list[dict],
    time_spent_min: int = 0,
    termination_reason: str | None = None,
    commit: bool = True,
) -> AssessmentSubmission:
    """Score and finalize an in-progress attempt (normal submit or auto-terminate)."""
    if existing_submission(db, assessment.id, profile.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assessment already submitted",
        )

    score, max_score = _score_answers(db, assessment, answers)
    submitted_at = datetime.now().isoformat(timespec="minutes")
    submission = existing_attempt(db, assessment.id, profile.id)
    answers_json = json.dumps(answers)
    if submission is None:
        submission = AssessmentSubmission(
            id=f"sub-{uuid.uuid4().hex[:8]}",
            assessment_id=assessment.id,
            student_id=profile.id,
            score=score,
            max_score=max_score,
            time_spent_min=time_spent_min,
            submitted_at=submitted_at,
            status="attended",
            answers=answers_json,
            termination_reason=termination_reason,
        )
        db.add(submission)
    else:
        submission.score = score
        submission.max_score = max_score
        submission.time_spent_min = time_spent_min
        submission.submitted_at = submitted_at
        submission.status = "attended"
        submission.answers = answers_json
        submission.remaining_seconds = 0
        submission.termination_reason = termination_reason

    end_session(
        db,
        assessment_id=assessment.id,
        student_id=profile.id,
        status_value="terminated" if termination_reason else "ended",
    )
    update_student_profile_after_submission(db, profile, score, max_score, submitted_at)
    update_assessment_class_avg(db, assessment.id)
    db.flush()
    build_and_store_assessment_report(db, assessment.id, profile.id, commit=False)
    if commit:
        db.commit()
        db.refresh(submission)
    return submission


def _parse_attempt_answers(sub: AssessmentSubmission | None) -> list[dict]:
    if sub is None or not sub.answers:
        return []
    try:
        raw = json.loads(sub.answers)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def record_violation(
    db: Session,
    *,
    assessment: Assessment,
    profile: StudentProfile,
    device_id: str,
    violation_type: str,
    occurred_at: str | None = None,
    user_agent: str | None = None,
) -> tuple[int, bool, AssessmentSubmission | None]:
    vtype = (violation_type or "").strip().upper()
    if vtype not in ALLOWED_VIOLATION_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported violation type: {violation_type}",
        )
    if assessment.mode == "practice":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Proctoring is not enabled for practice mode.",
        )
    if existing_submission(db, assessment.id, profile.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assessment already submitted.",
        )

    session = assert_active_device_session(db, assessment, profile.id, device_id)
    assert session is not None

    row = ExamViolation(
        id=f"ev-{uuid.uuid4().hex[:10]}",
        session_id=session.id,
        assessment_id=assessment.id,
        student_id=profile.id,
        violation_type=vtype,
        occurred_at=occurred_at or _now_iso(),
        user_agent=user_agent,
    )
    db.add(row)
    db.flush()
    count = violation_count(db, assessment.id, profile.id)
    terminated = count >= MAX_PROCTOR_VIOLATIONS
    submission: AssessmentSubmission | None = None
    if terminated:
        attempt = existing_attempt(db, assessment.id, profile.id)
        answers = _parse_attempt_answers(attempt)
        time_spent = 0
        if attempt and assessment.duration_minutes and attempt.remaining_seconds is not None:
            total = max(0, int(assessment.duration_minutes) * 60)
            time_spent = max(0, round((total - int(attempt.remaining_seconds or 0)) / 60))
        submission = finalize_attempt_as_attended(
            db,
            assessment=assessment,
            profile=profile,
            answers=answers,
            time_spent_min=time_spent,
            termination_reason=TERMINATION_REASON,
            commit=True,
        )
    else:
        db.commit()
    return count, terminated, submission


def list_violations(
    db: Session,
    *,
    assessment_id: str,
    student_id: str | None = None,
) -> list[ExamViolation]:
    q = db.query(ExamViolation).filter(ExamViolation.assessment_id == assessment_id)
    if student_id:
        q = q.filter(ExamViolation.student_id == student_id)
    return q.order_by(ExamViolation.occurred_at.asc()).all()
