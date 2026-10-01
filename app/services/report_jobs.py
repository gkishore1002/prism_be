"""Background jobs that extract score data and generate+store reports.

Flow:
1. Read attended assessments + uploaded marks from the DB
2. Map students to their batches (batch_students + assessment.batch_name / marks.batch_id)
3. Generate and persist assessment / overall / genome / cohort reports
4. Trigger only on write paths (submit, assessment completed, marks uploaded) — never on GET
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import BackgroundTasks, Request
from sqlalchemy.orm import Session

from app.models.assessment import Assessment, AssessmentSubmission
from app.models.content import Batch, BatchStudent
from app.models.marks import MarksEntry
from app.models.user import StudentProfile, User
from app.services.tenant_context import (
    close_tenant_db,
    open_tenant_db,
    safe_reset_tenant_context,
    set_tenant_context,
)

logger = logging.getLogger(__name__)

ReportJobKind = Literal[
    "assessment_completed",
    "assessment_submitted",
    "marks_uploaded",
    "batch_backfill",
]


def _batch_ids_for_student(db: Session, student_id: str) -> list[str]:
    return [
        row[0] if not hasattr(row, "batch_id") else row.batch_id
        for row in db.query(BatchStudent.batch_id)
        .filter(BatchStudent.student_id == student_id)
        .all()
    ]


def _resolve_batch_id_by_name(
    db: Session, institution_id: str, batch_name: str | None
) -> str | None:
    name = (batch_name or "").strip()
    if not name:
        return None
    batch = (
        db.query(Batch)
        .filter(Batch.institution_id == institution_id, Batch.name == name)
        .first()
    )
    return batch.id if batch else None


def student_score_flags(
    db: Session, institution_id: str, student_id: str
) -> dict[str, Any]:
    """Whether this student has completed assessments and/or uploaded marks."""
    attended = (
        db.query(AssessmentSubmission.assessment_id)
        .join(Assessment, Assessment.id == AssessmentSubmission.assessment_id)
        .filter(
            Assessment.institution_id == institution_id,
            AssessmentSubmission.student_id == student_id,
            AssessmentSubmission.status == "attended",
            AssessmentSubmission.max_score > 0,
            Assessment.mode != "practice",
        )
        .distinct()
        .all()
    )
    assessment_ids = [row[0] for row in attended]
    marks_count = (
        db.query(MarksEntry.id)
        .filter(
            MarksEntry.institution_id == institution_id,
            MarksEntry.student_id == student_id,
            MarksEntry.max_marks > 0,
        )
        .count()
    )
    return {
        "studentId": student_id,
        "batchIds": _batch_ids_for_student(db, student_id),
        "hasAssessments": bool(assessment_ids),
        "hasMarks": marks_count > 0,
        "attendedAssessmentIds": assessment_ids,
        "marksCount": marks_count,
    }


def extract_eligible_students(
    db: Session,
    institution_id: str,
    *,
    student_ids: list[str] | None = None,
    batch_id: str | None = None,
) -> list[dict[str, Any]]:
    """Students in scope who have at least one attended assessment or marks entry.

    When batch_id is set, membership comes from batch_students. Otherwise scopes to
    institution (optionally filtered to student_ids).
    """
    scoped_ids: list[str]
    if batch_id:
        scoped_ids = [
            row[0] if not hasattr(row, "student_id") else row.student_id
            for row in db.query(BatchStudent.student_id)
            .filter(BatchStudent.batch_id == batch_id)
            .all()
        ]
        if student_ids is not None:
            allow = set(student_ids)
            scoped_ids = [sid for sid in scoped_ids if sid in allow]
    elif student_ids is not None:
        scoped_ids = list(dict.fromkeys(student_ids))
    else:
        scoped_ids = [
            p.id
            for p in (
                db.query(StudentProfile)
                .join(User, User.id == StudentProfile.user_id)
                .filter(User.institution_id == institution_id)
                .all()
            )
        ]

    eligible: list[dict[str, Any]] = []
    for sid in scoped_ids:
        flags = student_score_flags(db, institution_id, sid)
        if flags["hasAssessments"] or flags["hasMarks"]:
            eligible.append(flags)
    return eligible


def _collect_batch_ids(
    db: Session,
    institution_id: str,
    student_ids: list[str],
    *,
    preferred_batch_id: str | None = None,
    assessment_batch_name: str | None = None,
) -> list[str]:
    batch_ids: set[str] = set()
    if preferred_batch_id:
        batch_ids.add(preferred_batch_id)
    resolved = _resolve_batch_id_by_name(db, institution_id, assessment_batch_name)
    if resolved:
        batch_ids.add(resolved)
    for sid in student_ids:
        batch_ids.update(_batch_ids_for_student(db, sid))
    return sorted(batch_ids)


def generate_and_store_reports(
    db: Session,
    institution_id: str,
    *,
    student_ids: list[str] | None = None,
    batch_id: str | None = None,
    assessment_id: str | None = None,
    use_ai: bool = True,
) -> dict[str, Any]:
    """Extract eligible students from DB and generate+store all report types."""
    from app.services.assessment_report import build_and_store_assessment_report
    from app.services.cohort_report import build_and_store_cohort_report
    from app.services.student_genome_report import build_and_store_genome_report
    from app.services.student_overall_report import build_and_store_overall_report

    assessment: Assessment | None = None
    if assessment_id:
        assessment = db.get(Assessment, assessment_id)
        if assessment and assessment.institution_id != institution_id:
            assessment = None

    # Narrow scope before extracting: never scan the whole institution unless asked.
    scoped_student_ids = student_ids
    if scoped_student_ids is None and assessment is not None:
        scoped_student_ids = [
            row.student_id
            for row in db.query(AssessmentSubmission.student_id)
            .filter(
                AssessmentSubmission.assessment_id == assessment.id,
                AssessmentSubmission.status == "attended",
            )
            .distinct()
            .all()
        ]

    eligible = extract_eligible_students(
        db,
        institution_id,
        student_ids=scoped_student_ids,
        batch_id=batch_id,
    )

    # Ensure attended students on this assessment are included even if extract
    # somehow missed them (e.g. race before score flags update).
    if assessment is not None:
        attended_ids = scoped_student_ids or []
        known = {row["studentId"] for row in eligible}
        for sid in attended_ids:
            if sid not in known:
                flags = student_score_flags(db, institution_id, sid)
                flags["hasAssessments"] = True
                if assessment.id not in flags["attendedAssessmentIds"]:
                    flags["attendedAssessmentIds"] = [
                        *flags["attendedAssessmentIds"],
                        assessment.id,
                    ]
                eligible.append(flags)

    refreshed_students: list[str] = []
    assessment_reports = 0
    full_batch_backfill = (
        assessment is None and batch_id is not None and student_ids is None
    )

    for row in eligible:
        sid = row["studentId"]
        try:
            # Assessment reports: only when an assessment was just scored/completed,
            # or during an explicit batch backfill.
            if assessment is not None:
                built = build_and_store_assessment_report(
                    db,
                    assessment.id,
                    sid,
                    force=True,
                    use_ai=use_ai,
                    commit=False,
                )
                if built:
                    assessment_reports += 1
            elif full_batch_backfill:
                for aid in row.get("attendedAssessmentIds") or []:
                    built = build_and_store_assessment_report(
                        db,
                        aid,
                        sid,
                        force=True,
                        use_ai=use_ai,
                        commit=False,
                    )
                    if built:
                        assessment_reports += 1

            if row.get("hasAssessments") or row.get("hasMarks"):
                build_and_store_overall_report(
                    db, sid, use_ai=use_ai, commit=False
                )
                build_and_store_genome_report(
                    db, institution_id, sid, use_ai=use_ai, commit=False
                )
                refreshed_students.append(sid)
        except Exception:  # noqa: BLE001
            logger.exception(
                "report_job_student_failed institution=%s student=%s assessment=%s",
                institution_id,
                sid,
                assessment_id,
            )

    batch_ids = _collect_batch_ids(
        db,
        institution_id,
        refreshed_students or [r["studentId"] for r in eligible],
        preferred_batch_id=batch_id,
        assessment_batch_name=assessment.batch_name if assessment else None,
    )
    refreshed_batches: list[str] = []
    for bid in batch_ids:
        try:
            build_and_store_cohort_report(
                db, institution_id, bid, commit=False
            )
            refreshed_batches.append(bid)
        except Exception:  # noqa: BLE001
            logger.exception(
                "report_job_cohort_failed institution=%s batch=%s",
                institution_id,
                bid,
            )

    db.commit()
    return {
        "eligibleStudents": len(eligible),
        "refreshedStudents": refreshed_students,
        "assessmentReports": assessment_reports,
        "refreshedBatches": refreshed_batches,
        "assessmentId": assessment_id,
        "batchId": batch_id,
    }


def run_after_assessment_completed(db: Session, assessment_id: str) -> dict[str, Any]:
    """Job entry: assessment marked completed — reports for every attended student."""
    assessment = db.get(Assessment, assessment_id)
    if not assessment:
        return {"eligibleStudents": 0, "refreshedStudents": [], "assessmentReports": 0}
    return generate_and_store_reports(
        db,
        assessment.institution_id,
        assessment_id=assessment_id,
        use_ai=True,
    )


def run_after_assessment_submitted(
    db: Session,
    institution_id: str,
    assessment_id: str,
    student_id: str,
) -> dict[str, Any]:
    """Job entry: one student submitted — store their assessment + insight reports."""
    return generate_and_store_reports(
        db,
        institution_id,
        student_ids=[student_id],
        assessment_id=assessment_id,
        use_ai=True,
    )


def run_after_marks_uploaded(
    db: Session,
    institution_id: str,
    student_ids: list[str],
    *,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Job entry: marks saved/published — only students with actual score data."""
    return generate_and_store_reports(
        db,
        institution_id,
        student_ids=student_ids,
        batch_id=batch_id,
        use_ai=True,
    )


def run_batch_backfill(
    db: Session,
    institution_id: str,
    batch_id: str,
    *,
    use_ai: bool = True,
) -> dict[str, Any]:
    """Job entry: scan a batch's students/marks/assessments and regenerate reports."""
    return generate_and_store_reports(
        db,
        institution_id,
        batch_id=batch_id,
        use_ai=use_ai,
    )


def execute_report_job(
    kind: ReportJobKind,
    *,
    schema_name: str | None,
    institution_id: str,
    assessment_id: str | None = None,
    student_id: str | None = None,
    student_ids: list[str] | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Tenant-aware runner used by FastAPI BackgroundTasks."""
    tokens = set_tenant_context(
        schema_name=schema_name or "public", institution_id=institution_id
    )
    db = open_tenant_db(schema_name)
    try:
        if kind == "assessment_completed":
            if not assessment_id:
                return {"error": "assessment_id required"}
            result = run_after_assessment_completed(db, assessment_id)
        elif kind == "assessment_submitted":
            if not assessment_id or not student_id:
                return {"error": "assessment_id and student_id required"}
            result = run_after_assessment_submitted(
                db, institution_id, assessment_id, student_id
            )
        elif kind == "marks_uploaded":
            result = run_after_marks_uploaded(
                db,
                institution_id,
                student_ids or [],
                batch_id=batch_id,
            )
        elif kind == "batch_backfill":
            if not batch_id:
                return {"error": "batch_id required"}
            result = run_batch_backfill(db, institution_id, batch_id)
        else:
            return {"error": f"unknown kind {kind}"}

        logger.info(
            "report_job_done kind=%s institution=%s students=%s batches=%s assessment_reports=%s",
            kind,
            institution_id,
            len(result.get("refreshedStudents") or []),
            len(result.get("refreshedBatches") or []),
            result.get("assessmentReports"),
        )
        return result
    except Exception:  # noqa: BLE001
        logger.exception(
            "report_job_failed kind=%s institution=%s assessment=%s",
            kind,
            institution_id,
            assessment_id,
        )
        return {"error": "report_job_failed"}
    finally:
        close_tenant_db(db)
        safe_reset_tenant_context(tokens)


def enqueue_report_job(
    background_tasks: BackgroundTasks,
    kind: ReportJobKind,
    request: Request | None,
    institution_id: str,
    *,
    assessment_id: str | None = None,
    student_id: str | None = None,
    student_ids: list[str] | None = None,
    batch_id: str | None = None,
) -> None:
    """Schedule a report generation job after the HTTP response."""
    schema_name = getattr(request.state, "tenant_schema", None) if request else None
    background_tasks.add_task(
        execute_report_job,
        kind,
        schema_name=schema_name,
        institution_id=institution_id,
        assessment_id=assessment_id,
        student_id=student_id,
        student_ids=student_ids,
        batch_id=batch_id,
    )
