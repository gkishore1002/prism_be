import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_roles
from app.core.routing import CamelCaseAPIRoute
from app.models.user import User
from app.schemas.base import CamelModel
from app.services import marks as marks_svc
from app.services.report_jobs import enqueue_report_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/marks", tags=["marks"], route_class=CamelCaseAPIRoute)


class MarksColumnIn(CamelModel):
    id: str
    subject: str
    conducted_on: str = Field(alias="conductedOn")
    max_marks: int = Field(alias="maxMarks")


class MarksBulkSaveIn(CamelModel):
    batch_id: str = Field(alias="batchId")
    assessment_title: str = Field(alias="assessmentTitle")
    description: str | None = None
    source: str = "manual"
    columns: list[MarksColumnIn]
    marks: dict[str, dict[str, str | float]]
    student_ids: list[str] = Field(alias="studentIds")


class MarksDraftSaveIn(CamelModel):
    batch_id: str | None = Field(default=None, alias="batchId")
    assessment_title: str = Field(default="", alias="assessmentTitle")
    description: str | None = None
    source: str = "manual"
    columns: list[MarksColumnIn] = Field(default_factory=list)
    marks: dict[str, dict[str, str | float]] = Field(default_factory=dict)
    student_ids: list[str] = Field(default_factory=list, alias="studentIds")


@router.get("")
def list_marks(
    batch_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> list:
    return marks_svc.list_marks_entries(db, user.institution_id, batch_id=batch_id)


@router.get("/sessions")
def list_marks_sessions(
    batch_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> list:
    return marks_svc.list_marks_sessions(db, user.institution_id, batch_id=batch_id)


@router.get("/drafts")
def list_marks_drafts(
    batch_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> list:
    return marks_svc.list_marks_drafts(
        db, user.institution_id, user_id=user.id, batch_id=batch_id
    )


@router.get("/drafts/{draft_id}")
def get_marks_draft(
    draft_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> dict:
    try:
        return marks_svc.get_marks_draft(
            db, user.institution_id, draft_id, user_id=user.id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/drafts", status_code=status.HTTP_201_CREATED)
def create_marks_draft(
    body: MarksDraftSaveIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> dict:
    try:
        return marks_svc.upsert_marks_draft(
            db,
            user.institution_id,
            user_id=user.id,
            draft_id=None,
            batch_id=body.batch_id,
            assessment_title=body.assessment_title,
            description=body.description,
            source=body.source,
            columns=[c.model_dump(by_alias=False) for c in body.columns],
            marks=body.marks,
            student_ids=body.student_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/drafts/{draft_id}")
def update_marks_draft(
    draft_id: str,
    body: MarksDraftSaveIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> dict:
    try:
        return marks_svc.upsert_marks_draft(
            db,
            user.institution_id,
            user_id=user.id,
            draft_id=draft_id,
            batch_id=body.batch_id,
            assessment_title=body.assessment_title,
            description=body.description,
            source=body.source,
            columns=[c.model_dump(by_alias=False) for c in body.columns],
            marks=body.marks,
            student_ids=body.student_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_marks_draft(
    draft_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> None:
    try:
        marks_svc.delete_marks_draft(db, user.institution_id, draft_id, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/drafts/{draft_id}/publish")
def publish_marks_draft(
    draft_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> dict:
    try:
        result = marks_svc.publish_marks_draft(
            db, user.institution_id, draft_id, user_id=user.id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    student_ids = sorted(
        {
            entry.get("studentId")
            for entry in (result.get("entries") or [])
            if entry.get("studentId")
        }
    )
    batch_id = result.get("batchId") or (
        (result.get("entries") or [{}])[0].get("batchId") if result.get("entries") else None
    )
    if student_ids:
        enqueue_report_job(
            background_tasks,
            "marks_uploaded",
            request,
            user.institution_id,
            student_ids=student_ids,
            batch_id=batch_id,
        )
    return result


@router.post("/bulk")
def save_marks_bulk(
    body: MarksBulkSaveIn,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> dict:
    try:
        result = marks_svc.save_marks_bulk(
            db,
            user.institution_id,
            batch_id=body.batch_id,
            assessment_title=body.assessment_title,
            description=body.description,
            source=body.source,
            created_by_user_id=user.id,
            columns=[c.model_dump(by_alias=False) for c in body.columns],
            marks=body.marks,
            student_ids=body.student_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if body.student_ids:
        enqueue_report_job(
            background_tasks,
            "marks_uploaded",
            request,
            user.institution_id,
            student_ids=list(body.student_ids),
            batch_id=body.batch_id,
        )
    return result


@router.get("/export")
def export_marks(
    batch_id: str | None = Query(None),
    session_id: str | None = Query(None),
    format: str = Query("xlsx", pattern="^(xlsx|csv)$"),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> Response:
    try:
        if format == "csv":
            payload, filename = marks_svc.export_marks_csv(
                db,
                user.institution_id,
                batch_id=batch_id,
                session_id=session_id,
            )
            return Response(
                content=payload,
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        payload, filename = marks_svc.export_marks_xlsx(
            db,
            user.institution_id,
            batch_id=batch_id,
            session_id=session_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
