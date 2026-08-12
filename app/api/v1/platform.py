"""Platform-level APIs for platform admins (SYSTEM login)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_effective_role, get_public_db, get_token_payload
from app.core.routing import CamelCaseAPIRoute
from app.models.institution import Institution
from app.models.super_admin import SuperAdmin
from app.models.user import User
from app.schemas import (
    PlatformOrganizationCreate,
    PlatformOrganizationDetailOut,
    PlatformOrganizationOut,
    PlatformOrganizationUpdate,
    PlatformStatsOut,
    PlatformSuperAdminCreate,
    PlatformSuperAdminOut,
)
from app.services.platform_auth import SUPER_USER_ROLE
from app.services.platform_organizations import (
    _count_organization_admins,
    create_organization,
    get_organization_detail,
    organization_out,
    update_organization,
)
from app.services.platform_super_admins import create_super_admin, list_super_admins

router = APIRouter(prefix="/platform", tags=["platform"], route_class=CamelCaseAPIRoute)


def _require_super_user(
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> User:
    if get_effective_role(payload, user) != SUPER_USER_ROLE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Platform access requires super user")
    return user


@router.get("/stats", response_model=PlatformStatsOut)
def platform_stats(
    public_db: Session = Depends(get_public_db),
    _: User = Depends(_require_super_user),
) -> PlatformStatsOut:
    org_count = public_db.query(Institution).count()
    active_org_count = public_db.query(Institution).filter(Institution.is_active.is_(True)).count()
    super_count = public_db.query(SuperAdmin).filter(SuperAdmin.is_active.is_(True)).count()
    return PlatformStatsOut(
        total_organizations=org_count,
        total_active_organizations=active_org_count,
        total_super_admins=super_count,
    )


@router.get("/super-admins", response_model=list[PlatformSuperAdminOut])
def list_platform_super_admins(
    public_db: Session = Depends(get_public_db),
    _: User = Depends(_require_super_user),
) -> list[PlatformSuperAdminOut]:
    return list_super_admins(public_db)


@router.post("/super-admins", response_model=PlatformSuperAdminOut, status_code=status.HTTP_201_CREATED)
def create_platform_super_admin(
    body: PlatformSuperAdminCreate,
    public_db: Session = Depends(get_public_db),
    _: User = Depends(_require_super_user),
) -> PlatformSuperAdminOut:
    return create_super_admin(
        public_db,
        email=str(body.email),
        full_name=body.full_name,
        password=body.password,
    )


@router.get("/organizations", response_model=list[PlatformOrganizationOut])
def list_platform_organizations(
    public_db: Session = Depends(get_public_db),
    _: User = Depends(_require_super_user),
) -> list[PlatformOrganizationOut]:
    rows = public_db.query(Institution).order_by(Institution.name).all()
    return [organization_out(inst, admin_count=_count_organization_admins(public_db, inst)) for inst in rows]


@router.post("/organizations", response_model=PlatformOrganizationOut, status_code=status.HTTP_201_CREATED)
def create_platform_organization(
    body: PlatformOrganizationCreate,
    public_db: Session = Depends(get_public_db),
    _: User = Depends(_require_super_user),
) -> PlatformOrganizationOut:
    return create_organization(
        public_db,
        organization_name=body.organization_name,
        organization_code=body.organization_code.strip().upper(),
        owner_name=body.owner_name,
        owner_phone=body.owner_phone,
        password=body.password,
        org_type=body.type,
    )


@router.get("/organizations/{code}", response_model=PlatformOrganizationDetailOut)
def get_platform_organization(
    code: str,
    public_db: Session = Depends(get_public_db),
    _: User = Depends(_require_super_user),
) -> PlatformOrganizationDetailOut:
    return get_organization_detail(public_db, code)


@router.patch("/organizations/{code}", response_model=PlatformOrganizationOut)
def patch_platform_organization(
    code: str,
    body: PlatformOrganizationUpdate,
    public_db: Session = Depends(get_public_db),
    _: User = Depends(_require_super_user),
) -> PlatformOrganizationOut:
    if body.name is None and body.type is None and body.is_active is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No changes provided")
    return update_organization(
        public_db,
        code,
        name=body.name,
        org_type=body.type,
        is_active=body.is_active,
    )
