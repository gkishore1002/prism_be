from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_public_db
from app.core.routing import CamelCaseAPIRoute
from app.schemas import SetupRequest, SetupStatusOut
from app.services.deployment import is_deployment_initialized
from app.services.setup import run_first_run_setup

router = APIRouter(prefix="/setup", tags=["setup"], route_class=CamelCaseAPIRoute)


@router.get("/status", response_model=SetupStatusOut)
def setup_status(public_db: Session = Depends(get_public_db)) -> SetupStatusOut:
    initialized = is_deployment_initialized(public_db)
    return SetupStatusOut(
        initialized=initialized,
        setup_required=not initialized,
        default_organization_code=settings.default_organization_code.upper(),
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def complete_setup(body: SetupRequest, public_db: Session = Depends(get_public_db)) -> dict:
    if is_deployment_initialized(public_db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Setup is not available after initialization")

    return run_first_run_setup(
        public_db,
        organization_name=body.organization_name,
        organization_code=body.organization_code,
        super_admin_name=body.super_admin_name,
        super_admin_phone=body.super_admin_phone,
        password=body.password,
    )
