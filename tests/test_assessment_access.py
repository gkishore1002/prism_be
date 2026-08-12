"""Assessment access and timing-over logic tests."""
from __future__ import annotations

from datetime import date, timedelta

from app.models.assessment import Assessment
from app.models.csc import AssessmentAccessRequest
from app.models.user import StudentProfile, User
from app.services.assessment_access import (
    can_student_attend,
    has_approved_extension,
    is_past_deadline,
)


def _assessment(**kwargs) -> Assessment:
    defaults = {
        "id": "a1",
        "institution_id": "inst-1",
        "title": "Test",
        "board": "CBSE",
        "grade": "Grade 8",
        "subject": "Math",
        "batch_name": "Batch A",
        "mode": "assessment",
        "status": "live",
        "scheduled_at": (date.today() - timedelta(days=5)).isoformat(),
        "available_until": (date.today() - timedelta(days=1)).isoformat(),
        "assigned_student_ids": '["stu-1"]',
    }
    defaults.update(kwargs)
    return Assessment(**defaults)


def test_timing_over_after_deadline(db):
    assessment = _assessment()
    db.add(assessment)
    db.commit()
    assert is_past_deadline(assessment, "stu-1", db) is True
    assert can_student_attend(db, assessment, "stu-1") is False


def test_practice_never_timing_over(db):
    assessment = _assessment(mode="practice")
    db.add(assessment)
    db.commit()
    assert is_past_deadline(assessment, "stu-1", db) is False


def test_approved_extension_restores_access(db):
    assessment = _assessment()
    req = AssessmentAccessRequest(
        id="req-1",
        assessment_id="a1",
        student_id="stu-1",
        reason="missed bus",
        status="approved",
        requested_at="2026-01-01T10:00",
        reviewed_at="2026-01-01T11:00",
        access_granted_until=(date.today() + timedelta(days=2)).isoformat(),
    )
    db.add_all([assessment, req])
    db.commit()
    assert has_approved_extension(db, "a1", "stu-1") is True
    assert is_past_deadline(assessment, "stu-1", db) is False
    assert can_student_attend(db, assessment, "stu-1") is True


def test_expired_extension_blocks_again(db):
    assessment = _assessment()
    req = AssessmentAccessRequest(
        id="req-2",
        assessment_id="a1",
        student_id="stu-1",
        reason="missed",
        status="approved",
        requested_at="2026-01-01T10:00",
        reviewed_at="2026-01-01T11:00",
        access_granted_until=(date.today() - timedelta(days=1)).isoformat(),
    )
    db.add_all([assessment, req])
    db.commit()
    assert has_approved_extension(db, "a1", "stu-1") is False
    assert is_past_deadline(assessment, "stu-1", db) is True
