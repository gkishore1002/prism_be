from fastapi import APIRouter, Depends, HTTPException, status

from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, get_public_db, get_effective_role, get_token_payload
from app.core.roles import get_allowed_roles, validate_role_selection
from app.core.routing import CamelCaseAPIRoute
from app.core.security import create_access_token, verify_password
from app.models.institution import Center, Institution
from app.models.super_admin import SuperAdmin
from app.models.user import StudentProfile, User
from app.schemas import (
    BranchContextOut,
    CenterOut,
    InstitutionOut,
    LoginAuthenticated,
    LoginOrganizationOut,
    LoginRequest,
    LoginRoleSelection,
    RoleOption,
    RoleOptionsOut,
    SelectRoleRequest,
    SwitchRoleRequest,
    UserOut,
)
from app.services.csc_eligibility import apply_csc_inactivity_check, get_csc_policy, login_block_message
from app.services.notification_dispatch import sync_staff_csc_notifications, sync_student_csc_notifications
from app.services.branch_access import (
    assigned_center_ids,
    get_accessible_center_ids,
    has_organization_wide_branch_access,
    is_organization_owner,
)
from app.services.centers import center_out_dict
from app.services.platform_auth import (
    SYSTEM_INSTITUTION_ID,
    SYSTEM_ORG_CODE,
    SUPER_USER_ROLE,
    is_system_org_code,
)
from app.services.tenant_context import (
    close_tenant_db,
    is_multi_schema_enabled,
    lookup_institution_by_code,
    open_tenant_db,
    safe_reset_tenant_context,
    set_tenant_context,
)
from app.utils import from_json_list

router = APIRouter(prefix="/auth", tags=["auth"], route_class=CamelCaseAPIRoute)

ROLE_LABELS: dict[str, tuple[str, str]] = {
    "student": ("Student", "Take tests, view progress and reports"),
    "tutor": ("Tutor", "Manage batches, assessments, and content"),
    "admin": ("Admin", "Organization oversight and analytics"),
}


def _system_institution() -> Institution:
    return Institution(
        id=SYSTEM_INSTITUTION_ID,
        name="Platform Admin",
        code=SYSTEM_ORG_CODE,
        schema_name="public",
        type="platform",
        board_ids="[]",
        policies_json="{}",
    )


def _resolve_user_and_institution(
    public_db: Session, email: str, institution_code: str | None
) -> tuple[User | None, SuperAdmin | None, Institution, Session, object | None]:
    from app.services.deployment import is_deployment_initialized

    if not is_deployment_initialized(public_db):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Deployment setup is required before login. Complete first-run setup at /setup.",
        )

    code = (institution_code or "").strip().upper()
    if is_system_org_code(code):
        super_admin = public_db.query(SuperAdmin).filter(SuperAdmin.email == email).first()
        if not super_admin or not super_admin.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
        return None, super_admin, _system_institution(), public_db, None

    org_count = public_db.query(Institution).count()
    if org_count > 1 and not institution_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select an organization to continue.",
        )

    if institution_code:
        institution = lookup_institution_by_code(public_db, institution_code)
    elif org_count == 1:
        institution = public_db.query(Institution).first()
    else:
        institution = None

    if not institution:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    if not bool(getattr(institution, "is_active", True)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization is inactive")

    schema_name = institution.schema_name or "public"
    if not is_multi_schema_enabled():
        user = public_db.query(User).filter(User.email == email, User.institution_id == institution.id).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
        return user, None, institution, public_db, None

    tokens = set_tenant_context(schema_name=schema_name, institution_id=institution.id)
    tenant_db = open_tenant_db(schema_name)
    user = tenant_db.query(User).filter(User.email == email, User.institution_id == institution.id).first()
    if not user:
        close_tenant_db(tenant_db)
        safe_reset_tenant_context(tokens)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return user, None, institution, tenant_db, tokens


def user_to_out(user: User, role: str | None = None, admin_portal: str | None = None) -> UserOut:
    effective_role = role or user.role
    return UserOut(
        id=user.id,
        name=user.name,
        email=user.email,
        role=effective_role,  # type: ignore[arg-type]
        avatar=user.avatar,
        institution_id=user.institution_id,
        grade_id=user.grade_id,
        board_id=user.board_id,
        is_owner=bool(getattr(user, "is_owner", False)),
        admin_portal=admin_portal if effective_role == "admin" else None,  # type: ignore[arg-type]
    )


def super_admin_to_out(super_admin: SuperAdmin) -> UserOut:
    return UserOut(
        id=super_admin.id,
        name=super_admin.full_name,
        email=super_admin.email,
        role="super_user",  # type: ignore[arg-type]
        avatar=None,
        institution_id=SYSTEM_INSTITUTION_ID,
        grade_id=None,
        board_id=None,
        is_owner=False,
    )


def _role_options_for(user: User, db: Session | None = None) -> list[RoleOption]:
    options: list[RoleOption] = []
    for r in get_allowed_roles(user):
        if r == "admin":
            if is_organization_owner(user, "admin"):
                options.append(
                    RoleOption(
                        role="admin",
                        label="Organization Admin",
                        description="Full organization settings, all branches, and analytics",
                        admin_portal="organization",
                    )
                )
                options.append(
                    RoleOption(
                        role="admin",
                        label="Branch Admin",
                        description="Admin tools scoped to assigned branches",
                        admin_portal="branch",
                    )
                )
            else:
                options.append(
                    RoleOption(
                        role="admin",
                        label="Branch Admin",
                        description="Admin tools for your assigned branches",
                        admin_portal="branch",
                    )
                )
        elif r in ROLE_LABELS:
            label, description = ROLE_LABELS[r]
            options.append(RoleOption(role=r, label=label, description=description))  # type: ignore[arg-type]
    return options


def _admin_token_scope(user: User, role: str, admin_portal: str | None) -> dict[str, str]:
    if role != "admin":
        return {}
    if admin_portal == "branch":
        return {"admin_scope": "branch"}
    if is_organization_owner(user, role):
        return {"admin_scope": "organization"}
    return {"admin_scope": "branch"}


def _resolve_admin_portal(user: User, role: str, admin_portal: str | None) -> str | None:
    if role != "admin":
        return None
    if admin_portal in ("organization", "branch"):
        return admin_portal
    if is_organization_owner(user, role):
        return "organization"
    return "branch"


def _login_authenticated_response(
    *,
    email: str,
    user: User,
    role: str,
    institution: Institution,
    admin_portal: str | None = None,
) -> LoginAuthenticated:
    resolved_portal = _resolve_admin_portal(user, role, admin_portal)
    token = create_access_token(
        {
            "sub": user.id,
            "role": role,
            "institution_id": institution.id,
            **_admin_token_scope(user, role, resolved_portal),
        }
    )
    return LoginAuthenticated(
        email=email,
        role=role,  # type: ignore[arg-type]
        user=user_to_out(user, role=role, admin_portal=resolved_portal),
        access_token=token,
        institution_code=institution.code,
        institution_name=institution.name,
        admin_portal=resolved_portal,  # type: ignore[arg-type]
    )


def _current_admin_portal(payload: dict) -> str | None:
    scope = payload.get("admin_scope")
    if scope in ("organization", "branch"):
        return scope
    return None


def _prepare_role_login(db: Session, user: User, role: str) -> None:
    """CSC eligibility check + in-app notification sync for the selected role."""
    if role == "student":
        profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
        if not profile:
            return
        apply_csc_inactivity_check(db, profile)
        db.refresh(profile)
        sync_student_csc_notifications(db, profile)
        db.commit()
        if profile.status == "inactive":
            policy = get_csc_policy(db, user.institution_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=login_block_message(profile, policy),
            )
    elif role in ("tutor", "admin"):
        sync_staff_csc_notifications(db, user, role)
        db.commit()


def _ensure_student_can_login(db: Session, user: User, role: str) -> None:
    _prepare_role_login(db, user, role)


@router.get("/organizations", response_model=list[LoginOrganizationOut])
def list_login_organizations(public_db: Session = Depends(get_public_db)) -> list[LoginOrganizationOut]:
    from app.services.deployment import is_deployment_initialized

    if not is_deployment_initialized(public_db):
        return []
    rows = (
        public_db.query(Institution)
        .filter(Institution.is_active.is_(True))
        .order_by(Institution.name)
        .all()
    )
    orgs = [
        LoginOrganizationOut(id=inst.id, name=inst.name, code=inst.code)
        for inst in rows
    ]
    if public_db.query(SuperAdmin).first():
        orgs.insert(
            0,
            LoginOrganizationOut(id=SYSTEM_INSTITUTION_ID, name="Platform Admin", code=SYSTEM_ORG_CODE),
        )
    return orgs


@router.post("/login", response_model=LoginAuthenticated | LoginRoleSelection)
def login(body: LoginRequest, public_db: Session = Depends(get_public_db)) -> LoginAuthenticated | LoginRoleSelection:
    email = body.email.strip().lower()
    user, super_admin, institution, tenant_db, tenant_tokens = _resolve_user_and_institution(
        public_db, email, body.institution_code
    )

    try:
        if super_admin:
            if not verify_password(body.password, super_admin.password_hash):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
            token = create_access_token(
                {"sub": super_admin.id, "role": SUPER_USER_ROLE, "institution_id": SYSTEM_INSTITUTION_ID}
            )
            out = super_admin_to_out(super_admin)
            return LoginAuthenticated(
                email=email,
                role=SUPER_USER_ROLE,  # type: ignore[arg-type]
                user=out,
                access_token=token,
                institution_code=institution.code,
                institution_name=institution.name,
            )

        assert user is not None
        if not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

        allowed = get_allowed_roles(user)
        if len(allowed) > 1:
            return LoginRoleSelection(
                email=email,
                roles=_role_options_for(user, tenant_db),
                institution_code=institution.code,
                institution_name=institution.name,
            )

        role = allowed[0]
        _ensure_student_can_login(tenant_db, user, role)
        return _login_authenticated_response(
            email=email,
            user=user,
            role=role,
            institution=institution,
        )
    finally:
        if tenant_db is not public_db:
            close_tenant_db(tenant_db)
        safe_reset_tenant_context(tenant_tokens)


@router.post("/select-role", response_model=LoginAuthenticated)
def select_role(body: SelectRoleRequest, public_db: Session = Depends(get_public_db)) -> LoginAuthenticated:
    email = body.email.strip().lower()
    institution_code = getattr(body, "institution_code", None)
    user, super_admin, institution, tenant_db, tenant_tokens = _resolve_user_and_institution(
        public_db, email, institution_code
    )
    if super_admin:
        if tenant_db is not public_db:
            close_tenant_db(tenant_db)
        safe_reset_tenant_context(tenant_tokens)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Role selection is not available for platform users")
    assert user is not None
    try:
        validate_role_selection(user, body.role)
    except ValueError as exc:
        if tenant_db is not public_db:
            close_tenant_db(tenant_db)
        safe_reset_tenant_context(tenant_tokens)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    try:
        _ensure_student_can_login(tenant_db, user, body.role)
        admin_portal = getattr(body, "admin_portal", None)
        return _login_authenticated_response(
            email=body.email,
            user=user,
            role=body.role,
            institution=institution,
            admin_portal=admin_portal,
        )
    finally:
        if tenant_db is not public_db:
            close_tenant_db(tenant_db)
        safe_reset_tenant_context(tenant_tokens)


@router.get("/role-options", response_model=RoleOptionsOut)
def list_role_options(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> RoleOptionsOut:
    if get_effective_role(payload, user) == SUPER_USER_ROLE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Role switching is not available")
    options = _role_options_for(user, db)
    return RoleOptionsOut(
        roles=options,
        current_role=get_effective_role(payload, user),  # type: ignore[arg-type]
        current_admin_portal=_current_admin_portal(payload),  # type: ignore[arg-type]
    )


@router.post("/switch-role", response_model=LoginAuthenticated)
def switch_role(
    body: SwitchRoleRequest,
    db: Session = Depends(get_db),
    public_db: Session = Depends(get_public_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> LoginAuthenticated:
    if get_effective_role(payload, user) == SUPER_USER_ROLE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Role switching is not available")
    try:
        validate_role_selection(user, body.role)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    institution = public_db.get(Institution, user.institution_id)
    if not institution:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    _ensure_student_can_login(db, user, body.role)
    return _login_authenticated_response(
        email=user.email,
        user=user,
        role=body.role,
        institution=institution,
        admin_portal=body.admin_portal,
    )


@router.get("/me", response_model=UserOut)
def me(
    user: User = Depends(get_current_user),
    public_db: Session = Depends(get_public_db),
    payload: dict = Depends(get_token_payload),
) -> UserOut:
    if get_effective_role(payload, user) == SUPER_USER_ROLE:
        super_admin = public_db.get(SuperAdmin, payload["sub"])
        if super_admin:
            return super_admin_to_out(super_admin)
    return user_to_out(
        user,
        role=get_effective_role(payload, user),
        admin_portal=_current_admin_portal(payload),
    )


@router.get("/branch-context", response_model=BranchContextOut)
def branch_context(
    db: Session = Depends(get_db),
    public_db: Session = Depends(get_public_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> BranchContextOut:
    role = get_effective_role(payload, user)
    if role == SUPER_USER_ROLE and user.institution_id == SYSTEM_INSTITUTION_ID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select an organization (X-Org-Code) to load branch context",
        )

    institution = public_db.get(Institution, user.institution_id)
    if not institution:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    accessible_ids = get_accessible_center_ids(db, user, role)
    centers_q = db.query(Center).filter(Center.institution_id == user.institution_id).order_by(Center.name)
    if accessible_ids is not None:
        if not accessible_ids:
            centers = []
        else:
            centers = centers_q.filter(Center.id.in_(accessible_ids)).all()
    else:
        centers = centers_q.all()

    student_center_id = None
    if role == "student":
        profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
        student_center_id = profile.center_id if profile else None

    owner = is_organization_owner(user, role)
    platform_super = role == SUPER_USER_ROLE
    show_org_code = owner or platform_super
    return BranchContextOut(
        organization=InstitutionOut(
            id=institution.id,
            name=institution.name,
            code=institution.code if show_org_code else None,
            type=institution.type,
            board_ids=from_json_list(institution.board_ids),
        ),
        role=role,  # type: ignore[arg-type]
        is_owner=owner,
        is_platform_super_user=platform_super,
        can_select_all_branches=has_organization_wide_branch_access(db, user, role),
        accessible_centers=[
            CenterOut(**center_out_dict(db, c, user.institution_id)) for c in centers
        ],
        student_center_id=student_center_id,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout() -> None:
    return None
