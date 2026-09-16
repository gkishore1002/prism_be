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
from app.services.csc_eligibility import days_until_csc_disable
from app.services.institution_policies import get_csc_policy


def _cell(value) -> str:
    """Normalize cell text so Excel does not treat embedded newlines as row breaks."""
    if value is None:
        return ""
    text = str(value).replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
    return text


def _csv_response(rows: list[list], headers: list[str], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    # CRLF + UTF-8 BOM so Excel on Windows opens columns/rows correctly.
    writer = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([_cell(h) for h in headers])
    writer.writerows([[_cell(v) for v in row] for row in rows])
    content = "\ufeff" + buf.getvalue()
    return StreamingResponse(
        iter([content.encode("utf-8")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _filter_by_center(students: list, center_id: str | None = None, center_ids: list[str] | None = None) -> list:
    if center_id:
        return [s for s in students if s.center_id == center_id]
    if center_ids is not None:
        return [s for s in students if s.center_id in center_ids]
    return students


def _format_center_label(center: Center | None, fallback: str = "") -> str:
    if not center:
        return fallback
    name = (center.name or "").strip()
    city = (center.city or "").strip()
    if not city:
        return name or fallback
    if city.lower() in name.lower():
        return name
    return f"{name} · {city}"


def export_students_csv(
    db: Session,
    institution_id: str,
    center_id: str | None = None,
    center_ids: list[str] | None = None,
    academic_year_id: str | None = None,
    search: str | None = None,
) -> StreamingResponse:
    from app.models.content import Batch, BatchStudent
    from app.models.enrollment import StudentEnrollment
    from app.services.student_master import apply_student_master_filters, student_master_base_query

    q = student_master_base_query(db, institution_id)
    q = apply_student_master_filters(
        q,
        search=search,
        center=center_id,
        academic_year_id=academic_year_id,
        institution_id=institution_id,
        db=db,
    )
    students = q.all()
    if center_id is None and center_ids is not None:
        students = [s for s in students if s.center_id in center_ids]

    centers = {
        c.id: c for c in db.query(Center).filter(Center.institution_id == institution_id).all()
    }
    batch_name_by_id = {
        b.id: b.name
        for b in db.query(Batch).filter(Batch.institution_id == institution_id).all()
    }
    memberships_by_student: dict[str, list[str]] = {}
    student_ids = [s.id for s in students]
    if student_ids:
        for row in (
            db.query(BatchStudent).filter(BatchStudent.student_id.in_(student_ids)).all()
        ):
            memberships_by_student.setdefault(row.student_id, []).append(row.batch_id)

    enrollment_by_student: dict[str, StudentEnrollment] = {}
    if academic_year_id:
        for enr in (
            db.query(StudentEnrollment)
            .filter(StudentEnrollment.academic_year_id == academic_year_id)
            .all()
        ):
            enrollment_by_student[enr.student_id] = enr

    rows = []
    for s in students:
        batch_ids = memberships_by_student.get(s.id, [])
        batch_names = [batch_name_by_id[bid] for bid in batch_ids if bid in batch_name_by_id]
        batch_label = ", ".join(batch_names) if batch_names else (s.batch or "")
        enr = enrollment_by_student.get(s.id)
        board = enr.board if enr else s.board
        grade = enr.grade if enr else s.grade
        center = centers.get(s.center_id) if s.center_id else None
        rows.append([
            s.user.name,
            s.school_name or "",
            board,
            grade,
            batch_label,
            _format_center_label(center, s.center_id or ""),
            s.status,
            s.user.email,
            s.id,
            s.disable_reason or "",
            s.last_csc_interaction_at or "",
            days_until_csc_disable(s, db=db) if s.last_csc_interaction_at else "",
        ])
    today = date.today().isoformat()
    return _csv_response(
        rows,
        [
            "Name",
            "School",
            "Board",
            "Grade",
            "Batch",
            "Branch",
            "Status",
            "Email",
            "Student ID",
            "Disable Reason",
            "Last CSC Visit",
            "Days Until Disable",
        ],
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
