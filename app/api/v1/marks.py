from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_roles
from app.core.routing import CamelCaseAPIRoute
from app.models.user import User
from app.schemas.base import CamelModel
from app.services import marks as marks_svc

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


@router.post("/bulk")
def save_marks_bulk(
    body: MarksBulkSaveIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> dict:
    try:
        return marks_svc.save_marks_bulk(
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
