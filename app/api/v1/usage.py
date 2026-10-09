"""Institution LLM token usage reporting (admin Settings → Token usage)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import (
    get_effective_role,
    get_public_db,
    get_token_payload,
    require_roles,
)
from app.core.routing import CamelCaseAPIRoute
from app.models.user import User
from app.services import llm_usage as usage_svc
from app.services.platform_auth import SUPER_USER_ROLE

router = APIRouter(prefix="/usage", tags=["token-usage"], route_class=CamelCaseAPIRoute)


@router.get("/institution/me")
def get_my_institution_usage(
    start: date | None = Query(None),
    end: date | None = Query(None),
    public_db: Session = Depends(get_public_db),
    user: User = Depends(require_roles("admin")),
    payload: dict = Depends(get_token_payload),
) -> dict:
    """Token usage for the current organization (admin / platform super user)."""
    role = get_effective_role(payload, user)
    if role not in ("admin", SUPER_USER_ROLE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    institution_id = user.institution_id
    if not institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution context")
    try:
        # Usage table lives in the public registry schema.
        return usage_svc.institution_usage(public_db, institution_id, start=start, end=end)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
