"""Persisted Learning Genome narratives for a student.

AI generation runs only when marks or assessments are updated.
Genome report reads use the stored narratives and never call Vertex.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.assessment import StudentGenomeReport
from app.models.user import StudentProfile
from app.services import vertex_summary as vertex_svc

logger = logging.getLogger(__name__)


def _rule_narrative(name: str, overall: int, rank: int) -> str:
    return (
        f"{name}'s Learning Genome overall score is {overall}%. "
        f"Class rank #{rank}. Review subject affinity and topic mastery for next focus areas."
    )


def _rule_narrative_ta(name: str, overall: int, rank: int) -> str:
    return (
        f"{name} அவர்களின் ஒட்டுமொத்த மதிப்பெண் {overall}% ஆகும். "
        f"வகுப்பில் #{rank} இடம். விரிவான பகுப்பாய்வுக்கு CSC மையத்தை அணுகவும்."
    )


def get_stored_genome_narratives(
    db: Session, student_id: str
) -> tuple[str | None, str | None, str]:
    row = db.get(StudentGenomeReport, student_id)
    if not row:
        return None, None, "rule-based"
    return (
        (row.narrative or "").strip() or None,
        (row.narrative_ta or "").strip() or None,
        row.narrative_source or "rule-based",
    )


def build_and_store_genome_report(
    db: Session,
    institution_id: str,
    student_id: str,
    *,
    use_ai: bool = True,
    commit: bool = True,
) -> StudentGenomeReport | None:
    """Compute genome metrics context and persist AI/rule narratives."""
    from app.services.cohort_report import get_student_genome

    # Metrics-only payload (GET path never calls Vertex).
    payload = get_student_genome(db, institution_id, student_id)
    if not payload:
        return None

    profile = db.get(StudentProfile, student_id)
    if not profile:
        return None

    genome = payload.get("profile") or {}
    overall = int(genome.get("overall") or profile.health or 0)
    rank = int(genome.get("rank") or 1)
    name = payload.get("name") or profile.user.name

    narrative = _rule_narrative(name, overall, rank)
    narrative_ta = _rule_narrative_ta(name, overall, rank)
    narrative_source = "rule-based"

    if use_ai and genome:
        narrative_context = {
            "studentName": name,
            "batchLabel": payload.get("batchLabel"),
            "totalStudents": payload.get("totalStudents") or 1,
            "profile": genome,
        }
        ai_narrative, ai_narrative_ta = vertex_svc.generate_pair_parallel(
            vertex_svc.generate_student_genome_narrative,
            vertex_svc.generate_student_genome_narrative_ta,
            narrative_context,
        )
        if ai_narrative:
            narrative = ai_narrative
            narrative_source = "vertex"
        if ai_narrative_ta:
            narrative_ta = ai_narrative_ta

    computed_at = datetime.now().isoformat(timespec="minutes")
    row = db.get(StudentGenomeReport, student_id)
    if row is None:
        row = StudentGenomeReport(
            student_id=student_id,
            narrative=narrative,
            narrative_ta=narrative_ta,
            narrative_source=narrative_source,
            computed_at=computed_at,
        )
        db.add(row)
    else:
        row.narrative = narrative
        row.narrative_ta = narrative_ta
        row.narrative_source = narrative_source
        row.computed_at = computed_at
        db.add(row)

    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def refresh_student_insight_reports(
    db: Session,
    institution_id: str,
    student_ids: list[str],
    *,
    use_ai: bool = True,
) -> list[str]:
    """Regenerate overall + genome AI insights and class insights for affected batches."""
    from app.services.cohort_report import refresh_cohort_reports_for_students
    from app.services.student_overall_report import build_and_store_overall_report

    refreshed: list[str] = []
    for student_id in student_ids:
        try:
            build_and_store_overall_report(db, student_id, use_ai=use_ai, commit=False)
            build_and_store_genome_report(
                db, institution_id, student_id, use_ai=use_ai, commit=False
            )
            refreshed.append(student_id)
        except Exception:  # noqa: BLE001
            logger.exception("Failed refreshing insight reports student=%s", student_id)
    try:
        refresh_cohort_reports_for_students(
            db, institution_id, student_ids, commit=False
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed refreshing cohort reports for students=%s", len(student_ids))
    db.commit()
    return refreshed
