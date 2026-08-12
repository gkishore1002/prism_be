from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_effective_role, get_token_payload, require_roles
from app.core.routing import CamelCaseAPIRoute
from app.models.user import User
from app.services.branch_access import assert_can_access_center, resolve_branch_filter
from app.services.exports import (
    export_centers_csv,
    export_csc_compliance_csv,
    export_reassignment_csv,
    export_students_csv,
)

router = APIRouter(prefix="/exports", tags=["exports"], route_class=CamelCaseAPIRoute)


@router.get("/students.csv")
def download_students_csv(
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "tutor")),
    payload: dict = Depends(get_token_payload),
):
    role = get_effective_role(payload, user)
    scope = resolve_branch_filter(db, user, role, center_id)
    return export_students_csv(db, user.institution_id, center_id=center_id, center_ids=scope)


@router.get("/csc-compliance.csv")
def download_csc_compliance_csv(
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "tutor")),
    payload: dict = Depends(get_token_payload),
):
    role = get_effective_role(payload, user)
    scope = resolve_branch_filter(db, user, role, center_id)
    return export_csc_compliance_csv(db, user.institution_id, center_id=center_id, center_ids=scope)


@router.get("/reassignment-requests.csv")
def download_reassignment_csv(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "tutor")),
):
    return export_reassignment_csv(db, user.institution_id)


@router.get("/centers.csv")
def download_centers_csv(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    return export_centers_csv(db, user.institution_id)
