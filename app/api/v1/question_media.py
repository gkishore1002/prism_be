"""Upload and serve question stem / option images."""

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_roles
from app.core.routing import CamelCaseAPIRoute
from app.models.user import User
from app.schemas import QuestionMediaOut
from app.services import question_media as media_svc

router = APIRouter(tags=["question-media"], route_class=CamelCaseAPIRoute)


@router.post("/question-media", response_model=QuestionMediaOut)
async def upload_question_media(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> QuestionMediaOut:
    key = await media_svc.save_upload(db, user.institution_id, file)
    db.commit()
    return QuestionMediaOut(key=key, url=media_svc.media_url_for_key(key) or "")


@router.get("/question-media/{institution_id}/{filename}")
def get_question_media(
    institution_id: str,
    filename: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    key = f"{institution_id}/{filename}"
    data, media_type = media_svc.load_media(db, key, user.institution_id)
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )
