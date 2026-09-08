"""Persisted overall performance insights for a student.

AI generation runs only when an assessment is submitted or marked completed.
Overall-report reads use the stored summaries and never call Vertex.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.assessment import StudentOverallReport
from app.models.user import StudentProfile
from app.services import vertex_summary as vertex_svc

logger = logging.getLogger(__name__)


def _rule_insight(profile: StudentProfile) -> str:
    return (
        f"{profile.user.name} is {'improving' if profile.improving else 'needs support'} "
        f"with {profile.critical_gaps} critical gaps."
    )


def _rule_insight_ta(profile: StudentProfile) -> str:
    return (
        f"{profile.user.name} அவர்களின் ஒட்டுமொத்த கற்றல் சுகாதாரம் {profile.health}% ஆக உள்ளது. "
        f"{'முன்னேற்றம் உள்ளது' if profile.improving else 'கவனம் தேவை'}. "
        f"{profile.critical_gaps} முக்கிய இடைவெளிகள் கண்டறியப்பட்டுள்ளன."
    )


def get_stored_overall_summaries(db: Session, student_id: str) -> tuple[str | None, str | None, str]:
    row = db.get(StudentOverallReport, student_id)
    if not row:
        return None, None, "rule-based"
    return (
        (row.summary or "").strip() or None,
        (row.summary_ta or "").strip() or None,
        row.summary_source or "rule-based",
    )


def build_and_store_overall_report(
    db: Session,
    student_id: str,
    *,
    use_ai: bool = True,
    commit: bool = True,
) -> StudentOverallReport | None:
    """Compute overall metrics context and persist AI/rule summaries."""
    from app.services import analytics as analytics_svc

    profile = db.get(StudentProfile, student_id)
    if not profile:
        return None

    # Live metrics for the AI prompt — summaries are what we persist.
    health = analytics_svc.get_student_health(db, student_id)
    gaps = analytics_svc.get_learning_gaps(db, student_id)
    readiness = analytics_svc.get_readiness_predictions(db, student_id)
    trend = analytics_svc.get_improvement_trend(db, student_id)
    topics = analytics_svc.get_topic_breakdown(db, student_id)
    monthly = analytics_svc.get_monthly_reports(db, student_id)
    recovery = analytics_svc.get_recovery_plan(db, student_id)
    subjects = analytics_svc.get_student_subjects(db, student_id)
    recent = analytics_svc.get_recent_assessments(db, student_id)
    wise = analytics_svc.get_student_wise_report(db, student_id) or {}

    context = {
        "studentName": profile.user.name,
        "board": profile.board,
        "grade": profile.grade,
        "batch": profile.batch,
        "health": profile.health,
        "healthStatus": profile.health_status,
        "readiness": profile.readiness,
        "improving": profile.improving,
        "criticalGaps": profile.critical_gaps,
        "subjectHealth": subjects,
        "overallHealth": health,
        "learningGaps": gaps[:5],
        "readinessPredictions": readiness,
        "improvementTrend": trend,
        "topicBreakdown": topics[:8],
        "monthlyReports": monthly,
        "recoveryPlan": recovery,
        "recentAssessments": recent,
        "strongTopics": wise.get("strongTopics", []),
        "weakTopics": wise.get("weakTopics", []),
        "avgAccuracy": wise.get("avgAccuracy", profile.health),
    }

    summary = _rule_insight(profile)
    summary_ta = _rule_insight_ta(profile)
    summary_source = "rule-based"

    if use_ai:
        ai_summary, ai_summary_ta = vertex_svc.generate_pair_parallel(
            vertex_svc.generate_student_report_summary,
            vertex_svc.generate_student_report_summary_ta,
            context,
        )
        if ai_summary:
            summary = ai_summary
            summary_source = "vertex"
        if ai_summary_ta:
            summary_ta = ai_summary_ta

    row = db.get(StudentOverallReport, student_id)
    computed_at = datetime.now().isoformat(timespec="minutes")
    if row is None:
        row = StudentOverallReport(
            student_id=student_id,
            summary=summary,
            summary_ta=summary_ta,
            summary_source=summary_source,
            computed_at=computed_at,
        )
        db.add(row)
    else:
        row.summary = summary
        row.summary_ta = summary_ta
        row.summary_source = summary_source
        row.computed_at = computed_at

    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row
