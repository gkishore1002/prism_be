from fastapi import APIRouter, Depends, HTTPException, status

from sqlalchemy.orm import Session



from app.core.deps import get_current_user, get_db, get_effective_role, get_token_payload

from app.core.roles import get_allowed_roles, validate_role_selection

from app.core.routing import CamelCaseAPIRoute

from app.core.security import create_access_token, verify_password

from app.models.institution import Institution

from app.models.user import User

from app.schemas import (

    LoginAuthenticated,

    LoginRequest,

    LoginRoleSelection,

    RoleOption,

    SelectRoleRequest,

    UserOut,

)



router = APIRouter(prefix="/auth", tags=["auth"], route_class=CamelCaseAPIRoute)



ROLE_LABELS: dict[str, tuple[str, str]] = {

    "student": ("Student", "Take tests, view progress and reports"),

    "tutor": ("Tutor", "Manage batches, assessments, and content"),

    "admin": ("Admin", "Institute oversight and analytics"),

}





def user_to_out(user: User, role: str | None = None) -> UserOut:

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

    )





def _role_options_for(user: User) -> list[RoleOption]:

    return [

        RoleOption(role=r, label=ROLE_LABELS[r][0], description=ROLE_LABELS[r][1])  # type: ignore[arg-type]

        for r in get_allowed_roles(user)

        if r in ROLE_LABELS

    ]





@router.post("/login", response_model=LoginAuthenticated | LoginRoleSelection)

def login(body: LoginRequest, db: Session = Depends(get_db)) -> LoginAuthenticated | LoginRoleSelection:

    email = body.email.strip().lower()

    institution = db.query(Institution).filter(Institution.code == body.institution_code.upper()).first()

    if not institution:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Institution not found. Use your institution code (e.g. BRIGHTPATH), not a center ID.",
        )



    user = db.query(User).filter(User.email == email, User.institution_id == institution.id).first()

    if not user or not verify_password(body.password, user.password_hash):

        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")



    allowed = get_allowed_roles(user)

    if len(allowed) > 1:

        return LoginRoleSelection(
            email=email,
            roles=_role_options_for(user),
            institution_code=institution.code,
            institution_name=institution.name,
        )



    role = allowed[0]

    token = create_access_token({"sub": user.id, "role": role, "institution_id": institution.id})

    out = user_to_out(user, role=role)

    return LoginAuthenticated(
        email=email,
        role=role,
        user=out,
        access_token=token,
        institution_code=institution.code,
        institution_name=institution.name,
    )  # type: ignore[arg-type]





@router.post("/select-role", response_model=LoginAuthenticated)

def select_role(body: SelectRoleRequest, db: Session = Depends(get_db)) -> LoginAuthenticated:

    email = body.email.strip().lower()

    user = db.query(User).filter(User.email == email).first()

    if not user:

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    try:

        validate_role_selection(user, body.role)

    except ValueError as exc:

        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc



    token = create_access_token(

        {"sub": user.id, "role": body.role, "institution_id": user.institution_id}

    )

    institution = db.get(Institution, user.institution_id)

    out = user_to_out(user, role=body.role)

    return LoginAuthenticated(
        email=body.email,
        role=body.role,
        user=out,
        access_token=token,
        institution_code=institution.code if institution else "",
        institution_name=institution.name if institution else "",
    )





@router.get("/me", response_model=UserOut)

def me(

    user: User = Depends(get_current_user),

    payload: dict = Depends(get_token_payload),

) -> UserOut:

    return user_to_out(user, role=get_effective_role(payload, user))





@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)

def logout() -> None:

    return None

