from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import uuid

from app.core.config import settings
from app.core.deps import get_current_user, get_db, require_roles
from app.core.routing import CamelCaseAPIRoute
from app.core.security import hash_password
from app.models.institution import Center, Institution
from app.models.user import StudentProfile, User
from app.schemas import CenterCreate, CenterOut, CenterUpdate, InstitutionOut, TutorCreate, TutorOut, TutorUpdate
from app.services.centers import center_out_dict, sync_center_counts
from app.utils import from_json_list

router = APIRouter(tags=["institutions"], route_class=CamelCaseAPIRoute)


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
    return InstitutionOut(
        id=inst.id,
        name=inst.name,
        code=inst.code,
        type=inst.type,
        board_ids=from_json_list(inst.board_ids),
    )


@router.get("/centers", response_model=list[CenterOut])
def list_centers(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[CenterOut]:
    centers = (
        db.query(Center)
        .filter(Center.institution_id == user.institution_id)
        .order_by(Center.name)
        .all()
    )
    return [CenterOut(**center_out_dict(db, c, user.institution_id)) for c in centers]


@router.post("/centers", response_model=CenterOut, status_code=status.HTTP_201_CREATED)
def create_center(
    body: CenterCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
) -> CenterOut:
    center = Center(
        id=f"ctr-{uuid.uuid4().hex[:8]}",
        institution_id=user.institution_id,
        name=body.name.strip(),
        city=(body.city or "").strip(),
    )
    db.add(center)
    db.commit()
    db.refresh(center)
    sync_center_counts(db, user.institution_id, commit=True)
    db.refresh(center)
    return CenterOut(**center_out_dict(db, center, user.institution_id))


@router.get("/centers/{center_id}", response_model=CenterOut)
def get_center(
    center_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CenterOut:
    return _get_center(db, center_id, user.institution_id)


@router.patch("/centers/{center_id}", response_model=CenterOut)
def update_center(
    center_id: str,
    body: CenterUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> CenterOut:
    center = _get_center(db, center_id, user.institution_id)
    if body.name is not None:
        center.name = body.name.strip()
    if body.city is not None:
        center.city = body.city.strip()
    db.commit()
    db.refresh(center)
    return center


@router.delete("/centers/{center_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_center(
    center_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
) -> None:
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
    if not tutor or tutor.institution_id != institution_id or tutor.role != "tutor":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tutor not found")
    return tutor


@router.get("/tutors", response_model=list[TutorOut])
def list_tutors(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
) -> list[TutorOut]:
    tutors = (
        db.query(User)
        .filter(User.institution_id == user.institution_id, User.role == "tutor")
        .order_by(User.name)
        .all()
    )
    return [_tutor_out(t) for t in tutors]


@router.post("/tutors", response_model=TutorOut, status_code=status.HTTP_201_CREATED)
def create_tutor(
    body: TutorCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
) -> TutorOut:
    email = body.email.strip().lower()
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")

    tutor = User(
        id=f"tut-{uuid.uuid4().hex[:8]}",
        institution_id=user.institution_id,
        name=body.name.strip(),
        email=email,
        password_hash=hash_password(settings.demo_password),
        role="tutor",
        roles="tutor",
    )
    db.add(tutor)
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
