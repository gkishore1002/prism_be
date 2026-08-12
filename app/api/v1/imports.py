"""Bulk import endpoints — students and staff via CSV templates."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_effective_role, get_token_payload, require_roles
from app.core.routing import CamelCaseAPIRoute
from app.models.user import User
from app.schemas import BulkImportResult, StaffBulkImportIn, StudentBulkImportIn
from app.services.branch_access import has_tenant_management_access
from app.services.bulk_import import (
    import_staff_bulk,
    import_students_bulk,
    staff_import_template_csv,
    student_import_template_csv,
)

router = APIRouter(prefix="/imports", tags=["imports"], route_class=CamelCaseAPIRoute)


def _csv_download(headers: list[str], rows: list[list[str]], filename: str) -> StreamingResponse:
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


@router.get("/students-template.csv")
def download_students_import_template(
    user: User = Depends(require_roles("admin", "tutor")),
) -> StreamingResponse:
    headers, rows = student_import_template_csv()
    return _csv_download(headers, rows, "students-import-template.csv")


@router.get("/staff-template.csv")
def download_staff_import_template(
    user: User = Depends(require_roles("admin")),
    payload: dict = Depends(get_token_payload),
) -> StreamingResponse:
    role = get_effective_role(payload, user)
    include_owner = has_tenant_management_access(user, role)
    headers, rows = staff_import_template_csv(include_org_owner=include_owner)
    return _csv_download(headers, rows, "staff-import-template.csv")


@router.post("/students", response_model=BulkImportResult)
def bulk_import_students(
    body: StudentBulkImportIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "tutor")),
    payload: dict = Depends(get_token_payload),
) -> BulkImportResult:
    if not body.rows:
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No rows to import")
    role = get_effective_role(payload, user)
    return import_students_bulk(
        db,
        institution_id=user.institution_id,
        actor=user,
        role=role,
        rows=body.rows,
    )


@router.post("/staff", response_model=BulkImportResult)
def bulk_import_staff(
    body: StaffBulkImportIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
    payload: dict = Depends(get_token_payload),
) -> BulkImportResult:
    if not body.rows:
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No rows to import")
    role = get_effective_role(payload, user)
    allow_org_owner = has_tenant_management_access(user, role)
    return import_staff_bulk(
        db,
        institution_id=user.institution_id,
        actor=user,
        role=role,
        rows=body.rows,
        allow_org_owner=allow_org_owner,
    )
