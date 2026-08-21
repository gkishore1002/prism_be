from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
import uuid

from app.core.config import settings
from app.core.deps import get_current_user, get_db, get_effective_role, get_token_payload, require_roles
from app.core.routing import CamelCaseAPIRoute
from app.core.security import hash_password
from app.models.institution import Center, Institution
from app.models.user import StudentProfile, User
from app.schemas import (
    CenterCreate,
    CenterOut,
    CenterUpdate,
    InstitutionOut,
    InstitutionPoliciesOut,
    InstitutionPoliciesUpdate,
    InstitutionUpdate,
    TutorCreate,
    TutorOut,
    TutorUpdate,
)
from app.services.institution_policies import (
    get_institution_policies,
    save_institution_policies,
    validate_assessment_policy,
    validate_csc_policy,
)
from app.services.audit_log import record_audit
from app.services.branch_access import (
    assert_can_access_center,
    can_access_center,
    get_accessible_center_ids,
    require_tenant_management_access,
    set_user_center_access,
)
from app.services.user_roles import add_role, filter_users_with_role, has_role, is_tutor_account
from app.services.platform_auth import SUPER_USER_ROLE
from app.services.centers import center_out_dict, sync_center_counts, validate_center_for_institution
from app.utils import from_json_list

router = APIRouter(tags=["institutions"], route_class=CamelCaseAPIRoute)


def _institution_out(inst: Institution, *, include_code: bool = False) -> InstitutionOut:
    return InstitutionOut(
        id=inst.id,
        name=inst.name,
        code=inst.code if include_code else None,
        type=inst.type,
        board_ids=from_json_list(inst.board_ids),
    )


def _get_center(db: Session, center_id: str, institution_id: str) -> Center:
    center = db.get(Center, center_id)
    if not center or center.institution_id != institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Center not found")
    return center


@router.get("/institutions/{code}", response_model=InstitutionOut)
def get_institution_by_code(code: str, db: Session = Depends(get_db)) -> InstitutionOut:
    inst = db.query(Institution).filter(Institution.code == code.upper()).first()
    if not inst:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Institution not found")
    return _institution_out(inst, include_code=False)


@router.get("/centers", response_model=list[CenterOut])
def list_centers(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> list[CenterOut]:
    role = get_effective_role(payload, user)
    accessible = get_accessible_center_ids(db, user, role)
    q = db.query(Center).filter(Center.institution_id == user.institution_id).order_by(Center.name)
    if accessible is not None:
        if not accessible:
            return []
        q = q.filter(Center.id.in_(accessible))
    centers = q.all()
    return [CenterOut(**center_out_dict(db, c, user.institution_id)) for c in centers]


@router.post("/centers", response_model=CenterOut, status_code=status.HTTP_201_CREATED)
def create_center(
    body: CenterCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
    payload: dict = Depends(get_token_payload),
) -> CenterOut:
    role = get_effective_role(payload, user)
    require_tenant_management_access(user, role)
    code = (body.code or "").strip().upper()
    if code:
        existing = (
            db.query(Center)
            .filter(Center.institution_id == user.institution_id, Center.code == code)
            .first()
        )
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Branch code already in use")
    center = Center(
        id=f"ctr-{uuid.uuid4().hex[:8]}",
        institution_id=user.institution_id,
        name=body.name.strip(),
        code=code or f"ctr-{uuid.uuid4().hex[:6]}".upper(),
        city=(body.city or "").strip(),
    )
    db.add(center)
    db.commit()
    db.refresh(center)
    sync_center_counts(db, user.institution_id, commit=True)
    db.refresh(center)
    record_audit(
        db,
        institution_id=user.institution_id,
        actor_user_id=user.id,
        actor_role=get_effective_role(payload, user),
        action="center_create",
        entity_type="center",
        entity_id=center.id,
        new_state={"name": center.name, "city": center.city, "active": center.active},
    )
    db.commit()
    return CenterOut(**center_out_dict(db, center, user.institution_id))


@router.get("/centers/{center_id}", response_model=CenterOut)
def get_center(
    center_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> CenterOut:
    center = _get_center(db, center_id, user.institution_id)
    role = get_effective_role(payload, user)
    assert_can_access_center(db, user, role, center_id)
    return CenterOut(**center_out_dict(db, center, user.institution_id))


@router.patch("/centers/{center_id}", response_model=CenterOut)
def update_center(
    center_id: str,
    body: CenterUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
    payload: dict = Depends(get_token_payload),
) -> CenterOut:
    center = _get_center(db, center_id, user.institution_id)
    role = get_effective_role(payload, user)
    if role == "admin" and not can_access_center(db, user, role, center_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Branch access denied")
    prev = {"name": center.name, "city": center.city, "active": center.active}
    if body.name is not None:
        center.name = body.name.strip()
    if body.city is not None:
        center.city = body.city.strip()
    if body.active is not None:
        center.active = body.active
    record_audit(
        db,
        institution_id=user.institution_id,
        actor_user_id=user.id,
        actor_role=get_effective_role(payload, user),
        action="center_update",
        entity_type="center",
        entity_id=center.id,
        previous_state=prev,
        new_state={"name": center.name, "city": center.city, "active": center.active},
    )
    db.commit()
    db.refresh(center)
    return CenterOut(**center_out_dict(db, center, user.institution_id))


@router.delete("/centers/{center_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_center(
    center_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
    payload: dict = Depends(get_token_payload),
) -> None:
    role = get_effective_role(payload, user)
    require_tenant_management_access(user, role)
    center = _get_center(db, center_id, user.institution_id)
    in_use = (
        db.query(StudentProfile)
        .filter(StudentProfile.center_id == center_id)
        .count()
    )
    if in_use:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete center while students are assigned to it",
        )
    db.delete(center)
    db.commit()


def _tutor_out(user: User) -> TutorOut:
    return TutorOut(id=user.id, name=user.name, email=user.email)


def _get_tutor(db: Session, tutor_id: str, institution_id: str) -> User:
    tutor = db.get(User, tutor_id)
    if not tutor or tutor.institution_id != institution_id or not is_tutor_account(tutor):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tutor not found")
    return tutor


@router.get("/tutors", response_model=list[TutorOut])
def list_tutors(
    center_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
    payload: dict = Depends(get_token_payload),
) -> list[TutorOut]:
    from app.services.branch_access import resolve_branch_filter, user_matches_center_scope

    role = get_effective_role(payload, user)
    tutors = (
        filter_users_with_role(
            db.query(User).filter(User.institution_id == user.institution_id),
            "tutor",
        )
        .order_by(User.name)
        .all()
    )
    scope = resolve_branch_filter(db, user, role, center_id)
    return [_tutor_out(t) for t in tutors if user_matches_center_scope(db, t, scope)]


@router.post("/tutors", response_model=TutorOut, status_code=status.HTTP_201_CREATED)
def create_tutor(
    body: TutorCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
) -> TutorOut:
    from app.services.user_credentials import resolve_user_credentials

    email, password = resolve_user_credentials(phone=body.phone, password=body.password)
    existing = db.query(User).filter(User.email == email).first()

    if existing:
        if existing.institution_id != user.institution_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Phone number already registered")
        if has_role(existing, "student") and not has_role(existing, "tutor"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This account is a student profile. Use a separate staff phone number.",
            )
        add_role(existing, "tutor")
        if body.also_admin:
            add_role(existing, "admin")
            if body.is_owner:
                existing.is_owner = True
        if not existing.is_owner and body.center_ids:
            set_user_center_access(db, user=existing, center_ids=body.center_ids, actor=user)
        if body.name.strip():
            existing.name = body.name.strip()
        db.commit()
        db.refresh(existing)
        return _tutor_out(existing)

    initial_roles = ["tutor"]
    if body.also_admin:
        initial_roles.insert(0, "admin")
    tutor = User(
        id=f"tut-{uuid.uuid4().hex[:8]}",
        institution_id=user.institution_id,
        name=body.name.strip(),
        email=email,
        password_hash=hash_password(password),
        role=initial_roles[0],
        roles=",".join(initial_roles),
        is_owner=body.is_owner if body.also_admin else False,
    )
    db.add(tutor)
    db.flush()
    if not tutor.is_owner and body.center_ids:
        set_user_center_access(db, user=tutor, center_ids=body.center_ids, actor=user)
    db.commit()
    db.refresh(tutor)
    return _tutor_out(tutor)


@router.patch("/tutors/{tutor_id}", response_model=TutorOut)
def update_tutor(
    tutor_id: str,
    body: TutorUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
) -> TutorOut:
    tutor = _get_tutor(db, tutor_id, user.institution_id)
    if body.email is not None:
        email = body.email.strip().lower()
        conflict = (
            db.query(User)
            .filter(User.email == email, User.id != tutor_id)
            .first()
        )
        if conflict:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
        tutor.email = email
    if body.name is not None:
        tutor.name = body.name.strip()
    db.commit()
    db.refresh(tutor)
    return _tutor_out(tutor)


@router.get("/institution", response_model=InstitutionOut)
def get_current_institution(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> InstitutionOut:
    role = get_effective_role(payload, user)
    inst = db.get(Institution, user.institution_id)
    if not inst:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    include_code = (role == "admin" and bool(getattr(user, "is_owner", False))) or role == SUPER_USER_ROLE
    return _institution_out(inst, include_code=include_code)


@router.patch("/institution", response_model=InstitutionOut)
def update_current_institution(
    body: InstitutionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
    payload: dict = Depends(get_token_payload),
) -> InstitutionOut:
    role = get_effective_role(payload, user)
    require_tenant_management_access(user, role)
    inst = db.get(Institution, user.institution_id)
    if not inst:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    prev = {"name": inst.name, "type": inst.type}
    if body.name is not None:
        inst.name = body.name.strip()
    if body.type is not None:
        inst.type = body.type.strip()
    record_audit(
        db,
        institution_id=user.institution_id,
        actor_user_id=user.id,
        actor_role=role,
        action="organization_update",
        entity_type="organization",
        entity_id=inst.id,
        previous_state=prev,
        new_state={"name": inst.name, "type": inst.type},
    )
    db.commit()
    db.refresh(inst)
    return _institution_out(inst, include_code=True)


@router.get("/institution/policies", response_model=InstitutionPoliciesOut)
def get_policies(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> InstitutionPoliciesOut:
    data = get_institution_policies(db, user.institution_id)
    return InstitutionPoliciesOut(assessment=data["assessment"], csc=data["csc"])


@router.put("/institution/policies", response_model=InstitutionPoliciesOut)
def update_policies(
    body: InstitutionPoliciesUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
    payload: dict = Depends(get_token_payload),
) -> InstitutionPoliciesOut:
    prev = get_institution_policies(db, user.institution_id)
    assessment_patch = None
    csc_patch = None
    try:
        if body.assessment is not None:
            assessment_patch = validate_assessment_policy(body.assessment)
        if body.csc is not None:
            csc_patch = validate_csc_policy(body.csc)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    data = save_institution_policies(
        db,
        user.institution_id,
        assessment=assessment_patch,
        csc=csc_patch,
    )
    record_audit(
        db,
        institution_id=user.institution_id,
        actor_user_id=user.id,
        actor_role=get_effective_role(payload, user),
        action="settings_update",
        entity_type="institution",
        entity_id=user.institution_id,
        previous_state=prev,
        new_state=data,
    )
    db.commit()
    return InstitutionPoliciesOut(assessment=data["assessment"], csc=data["csc"])
