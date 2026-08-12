"""Center helpers — live student counts from profiles."""

from __future__ import annotations

from collections import defaultdict

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.institution import Center
from app.models.user import StudentProfile, User


def validate_center_for_institution(
    db: Session,
    center_id: str,
    institution_id: str,
    *,
    allow_inactive: bool = False,
) -> Center:
    center = db.get(Center, center_id)
    if not center or center.institution_id != institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Center not found")
    if not allow_inactive and not center.active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Center is inactive. Assign students to an active branch.",
        )
    return center


def student_counts_by_center(db: Session, institution_id: str) -> dict[str, int]:
    rows = (
        db.query(StudentProfile.center_id)
        .join(User)
        .filter(User.institution_id == institution_id)
        .all()
    )
    counts: dict[str, int] = defaultdict(int)
    for (center_id,) in rows:
        if center_id:
            counts[center_id] += 1
    return dict(counts)


def sync_center_counts(db: Session, institution_id: str, *, commit: bool = False) -> None:
    counts = student_counts_by_center(db, institution_id)
    centers = db.query(Center).filter(Center.institution_id == institution_id).all()
    for center in centers:
        center.student_count = counts.get(center.id, 0)
    if commit:
        db.commit()


def center_out_dict(db: Session, center: Center, institution_id: str) -> dict:
    counts = student_counts_by_center(db, institution_id)
    return {
        "id": center.id,
        "name": center.name,
        "code": center.code or center.id,
        "city": center.city or "",
        "active": bool(center.active),
        "student_count": counts.get(center.id, 0),
        "batch_count": center.batch_count or 0,
    }
