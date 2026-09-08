"""Upload and serve question stem / option images."""

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse

from app.core.deps import get_current_user, require_roles
from app.core.routing import CamelCaseAPIRoute
from app.models.user import User
from app.schemas import QuestionMediaOut
from app.services import question_media as media_svc

router = APIRouter(tags=["question-media"], route_class=CamelCaseAPIRoute)


@router.post("/question-media", response_model=QuestionMediaOut)
async def upload_question_media(
    file: UploadFile = File(...),
    user: User = Depends(require_roles("tutor", "admin")),
) -> QuestionMediaOut:
    key = await media_svc.save_upload(user.institution_id, file)
    return QuestionMediaOut(key=key, url=media_svc.media_url_for_key(key) or "")


@router.get("/question-media/{institution_id}/{filename}")
def get_question_media(
    institution_id: str,
    filename: str,
    user: User = Depends(get_current_user),
) -> FileResponse:
    key = f"{institution_id}/{filename}"
    media_svc.assert_key_owned(key, user.institution_id)
    path = media_svc.resolve_media_path(key)
    media_type = "image/jpeg"
    if path.suffix == ".png":
        media_type = "image/png"
    elif path.suffix == ".webp":
        media_type = "image/webp"
    return FileResponse(path, media_type=media_type)
