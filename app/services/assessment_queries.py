"""Shared assessment list queries for portal bootstrap and API routes."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.assessment import Assessment, AssessmentSubmission
from app.utils import from_json_list


def _grades_match(a: str, b: str) -> bool:
    def norm(g: str) -> str:
        match = re.search(r"\d+", g or "")
        return match.group(0) if match else (g or "").strip()

    return norm(a) == norm(b)


def _boards_match(a: str, b: str) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def list_assessments_for_student(
    db: Session,
    institution_id: str,
    student_id: str,
    *,
    board: str | None = None,
    grade: str | None = None,
) -> list[tuple[Assessment, bool, bool]]:
    """Return (assessment, student_submitted, attempt_in_progress)."""
    attempts = (
        db.query(AssessmentSubmission)
        .filter(AssessmentSubmission.student_id == student_id)
        .all()
    )
    submitted_ids = {
        s.assessment_id for s in attempts if s.status in ("attended", "absent")
    }
    in_progress_ids = {
        s.assessment_id for s in attempts if s.status == "in_progress"
    }

    results: list[tuple[Assessment, bool, bool]] = []
    query = (
        db.query(Assessment)
        .filter(Assessment.institution_id == institution_id)
        .order_by(Assessment.scheduled_at.desc())
    )
    for assessment in query.all():
        if student_id not in from_json_list(assessment.assigned_student_ids):
            continue
        if assessment.status == "draft":
            continue
        if board and not _boards_match(assessment.board, board):
            continue
        if grade and not _grades_match(assessment.grade, grade):
            continue
        results.append(
            (
                assessment,
                assessment.id in submitted_ids,
                assessment.id in in_progress_ids,
            )
        )
    return results
