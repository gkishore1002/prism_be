from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, get_effective_role, get_token_payload
from app.core.routing import CamelCaseAPIRoute
from app.models.user import User
from app.services import portal_bootstrap as bootstrap_svc

router = APIRouter(prefix="/portal", tags=["portal"], route_class=CamelCaseAPIRoute)


@router.get("/bootstrap")
def portal_bootstrap(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> dict:
    """Single request that replaces 15–20 parallel calls on login."""
    role = get_effective_role(payload, user)
    return bootstrap_svc.portal_bootstrap(db, user, role)
