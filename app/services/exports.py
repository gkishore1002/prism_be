"""CSV export helpers for admin operational reports."""
from __future__ import annotations

import csv
import io
from datetime import date

from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.models.assessment import Assessment
from app.models.csc import AssessmentAccessRequest
from app.models.institution import Center
from app.models.user import StudentProfile, User
from app.services.analytics import _students_for_institution
from app.services.csc_eligibility import days_until_csc_disable
from app.services.institution_policies import get_csc_policy


def _csv_response(rows: list[list], headers: list[str], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    content = buf.getvalue()
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _filter_by_center(students: list, center_id: str | None = None, center_ids: list[str] | None = None) -> list:
    if center_id:
        return [s for s in students if s.center_id == center_id]
    if center_ids is not None:
        return [s for s in students if s.center_id in center_ids]
    return students


def export_students_csv(
    db: Session,
    institution_id: str,
    center_id: str | None = None,
    center_ids: list[str] | None = None,
) -> StreamingResponse:
    students = _filter_by_center(_students_for_institution(db, institution_id), center_id, center_ids)
    centers = {c.id: c.name for c in db.query(Center).filter(Center.institution_id == institution_id).all()}
    rows = []
    for s in students:
        rows.append([
            s.id,
            s.user.name,
            s.user.email,
            s.board,
            s.grade,
            s.batch,
            centers.get(s.center_id, s.center_id or ""),
            s.status,
            s.disable_reason or "",
            s.last_csc_interaction_at or "",
            days_until_csc_disable(s, db=db) if s.last_csc_interaction_at else "",
        ])
    today = date.today().isoformat()
    return _csv_response(
        rows,
        ["Student ID", "Name", "Email", "Board", "Grade", "Batch", "Center", "Status", "Disable Reason", "Last CSC Visit", "Days Until Disable"],
        f"students-{today}.csv",
    )


def export_csc_compliance_csv(
    db: Session,
    institution_id: str,
    center_id: str | None = None,
    center_ids: list[str] | None = None,
) -> StreamingResponse:
    policy = get_csc_policy(db, institution_id)
    students = _filter_by_center(_students_for_institution(db, institution_id), center_id, center_ids)
    centers = {c.id: c.name for c in db.query(Center).filter(Center.institution_id == institution_id).all()}
    rows = []
    for s in students:
        days = days_until_csc_disable(s, db=db)
        if days is None and not s.last_csc_interaction_at:
            csc_status = "never_visited"
        elif s.disable_reason == "csc_inactivity":
            csc_status = "inactive"
        elif days is not None and days <= policy.warning_threshold_days:
            csc_status = "due_soon"
        elif days is not None:
            csc_status = "active"
        else:
            csc_status = "unknown"
        rows.append([
            s.id,
            s.user.name,
            centers.get(s.center_id, s.center_id or ""),
            csc_status,
            s.last_csc_interaction_at or "",
            days if days is not None else "",
            s.status,
        ])
    today = date.today().isoformat()
    return _csv_response(
        rows,
        ["Student ID", "Name", "Center", "CSC Status", "Last Visit", "Days Left", "Account Status"],
        f"csc-compliance-{today}.csv",
    )


def export_reassignment_csv(db: Session, institution_id: str) -> StreamingResponse:
    reqs = (
        db.query(AssessmentAccessRequest)
        .join(Assessment, Assessment.id == AssessmentAccessRequest.assessment_id)
        .filter(Assessment.institution_id == institution_id)
        .order_by(AssessmentAccessRequest.requested_at.desc())
        .all()
    )
    rows = []
    for req in reqs:
        assessment = db.get(Assessment, req.assessment_id)
        profile = db.get(StudentProfile, req.student_id)
        reviewer = db.get(User, req.reviewed_by) if req.reviewed_by else None
        rows.append([
            req.id,
            profile.user.name if profile else req.student_id,
            req.student_id,
            assessment.title if assessment else req.assessment_id,
            req.status,
            req.requested_at,
            req.reviewed_at or "",
            reviewer.name if reviewer else "",
            req.access_granted_until or "",
            (req.reason or "")[:200],
        ])
    today = date.today().isoformat()
    return _csv_response(
        rows,
        ["Request ID", "Student", "Student ID", "Assessment", "Status", "Requested", "Reviewed", "Reviewer", "Access Until", "Reason"],
        f"reassignment-requests-{today}.csv",
    )


def export_centers_csv(db: Session, institution_id: str) -> StreamingResponse:
    from app.services.centers import student_counts_by_center

    counts = student_counts_by_center(db, institution_id)
    centers = db.query(Center).filter(Center.institution_id == institution_id).order_by(Center.name).all()
    rows = []
    for c in centers:
        rows.append([
            c.id,
            c.name,
            c.city,
            "active" if c.active else "inactive",
            counts.get(c.id, 0),
        ])
    today = date.today().isoformat()
    return _csv_response(
        rows,
        ["Center ID", "Name", "City", "Status", "Students"],
        f"centers-{today}.csv",
    )
