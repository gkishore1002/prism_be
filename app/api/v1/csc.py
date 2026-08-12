import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, get_effective_role, get_token_payload, require_roles
from app.core.routing import CamelCaseAPIRoute
from app.models.csc import ReportCollectionLog
from app.models.user import StudentProfile, User
from app.schemas import ReportCollectionCreate, ReportCollectionOut, StudentTrackingOut
from app.services.audit_log import record_audit
from app.services.csc_eligibility import record_csc_interaction
from app.services.student_tracking import student_tracking_payload

router = APIRouter(prefix="/students", tags=["csc"], route_class=CamelCaseAPIRoute)


def _collection_out(db: Session, log: ReportCollectionLog) -> ReportCollectionOut:
    collector = db.get(User, log.collected_by_user_id)
    return ReportCollectionOut(
        id=log.id,
        student_id=log.student_id,
        report_kind=log.report_kind,  # type: ignore[arg-type]
        report_ref=log.report_ref,
        collected_at=log.collected_at,
        collected_by_user_id=log.collected_by_user_id,
        collected_by_name=collector.name if collector else log.collected_by_user_id,
        guardian_name=log.guardian_name,
        notes=log.notes,
    )


@router.get("/{student_id}/report-collections", response_model=list[ReportCollectionOut])
def list_report_collections(
    student_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "tutor")),
) -> list[ReportCollectionOut]:
    profile = db.get(StudentProfile, student_id)
    if not profile or profile.user.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    rows = (
        db.query(ReportCollectionLog)
        .filter(ReportCollectionLog.student_id == student_id)
        .order_by(ReportCollectionLog.collected_at.desc())
        .all()
    )
    return [_collection_out(db, r) for r in rows]


@router.post(
    "/{student_id}/report-collections",
    response_model=ReportCollectionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_report_collection(
    student_id: str,
    body: ReportCollectionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "tutor")),
    payload: dict = Depends(get_token_payload),
) -> ReportCollectionOut:
    profile = db.get(StudentProfile, student_id)
    if not profile or profile.user.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    collected_at = body.collected_at.strip() or datetime.now().isoformat(timespec="minutes")
    prev = {
        "lastCscInteractionAt": profile.last_csc_interaction_at,
        "status": profile.status,
        "disableReason": profile.disable_reason,
    }
    log = ReportCollectionLog(
        id=f"rcl-{uuid.uuid4().hex[:8]}",
        student_id=student_id,
        report_kind=body.report_kind,
        report_ref=body.report_ref or "",
        collected_at=collected_at,
        collected_by_user_id=user.id,
        guardian_name=body.guardian_name,
        notes=body.notes,
    )
    db.add(log)
    record_csc_interaction(db, profile, collected_at)
    record_audit(
        db,
        institution_id=user.institution_id,
        actor_user_id=user.id,
        actor_role=get_effective_role(payload, user),
        action="report_collection",
        entity_type="student",
        entity_id=student_id,
        previous_state=prev,
        new_state={
            "lastCscInteractionAt": profile.last_csc_interaction_at,
            "status": profile.status,
            "disableReason": profile.disable_reason,
            "collectionId": log.id,
        },
        notes=body.notes,
    )
    db.commit()
    db.refresh(log)
    return _collection_out(db, log)


@router.get("/{student_id}/tracking", response_model=StudentTrackingOut)
def get_student_tracking(
    student_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "tutor")),
) -> StudentTrackingOut:
    profile = db.get(StudentProfile, student_id)
    if not profile or profile.user.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    return StudentTrackingOut(**student_tracking_payload(db, profile))
