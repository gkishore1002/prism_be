"""Center helpers — live student counts from profiles."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.institution import Center
from app.models.user import StudentProfile, User


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
        "city": center.city or "",
        "student_count": counts.get(center.id, 0),
        "batch_count": center.batch_count or 0,
    }
