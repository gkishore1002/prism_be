import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, get_effective_role, get_token_payload, require_roles
from app.core.pagination import PaginatedOut, paginate_query
from app.core.routing import CamelCaseAPIRoute
from app.models.notification import Notification
from app.models.user import User
from app.schemas import NotificationCreate, NotificationOut
from app.services.notification_dispatch import create_user_notification

router = APIRouter(prefix="/notifications", tags=["notifications"], route_class=CamelCaseAPIRoute)


def _notification_query(db: Session, user: User, role: str | None):
    q = db.query(Notification).filter(Notification.institution_id == user.institution_id)
    q = q.filter(
        or_(
            Notification.user_id == user.id,
            Notification.user_id.is_(None),
        )
    )
    if role:
        q = q.filter(Notification.role == role)
    return q.order_by(Notification.created_at.desc())


@router.get("", response_model=PaginatedOut[NotificationOut] | list[NotificationOut])
def list_notifications(
    role: str | None = Query(None),
    page: int | None = Query(None, ge=1),
    limit: int | None = Query(None, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> PaginatedOut[NotificationOut] | list[NotificationOut]:
    effective = role or get_effective_role(payload, user)
    q = _notification_query(db, user, effective)
    if page is None and limit is None:
        return q.all()
    items, total, page_n, limit_n, pages = paginate_query(q, page or 1, limit)
    return PaginatedOut(items=items, total=total, page=page_n, limit=limit_n, pages=pages)


@router.get("/unread-count", response_model=dict)
def unread_count(
    role: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
) -> dict:
    effective = role or get_effective_role(payload, user)
    count = (
        _notification_query(db, user, effective)
        .filter(Notification.read.is_(False))
        .count()
    )
    return {"count": count}


@router.post("", response_model=NotificationOut, status_code=status.HTTP_201_CREATED)
def create_notification(
    body: NotificationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> NotificationOut:
    note = create_user_notification(
        db,
        user=user,
        role=body.role,
        ntype=body.type,
        title=body.title,
        message=body.message,
        kind=body.kind,
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        href=body.href,
    )
    if note is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Duplicate notification")
    db.commit()
    db.refresh(note)
    return note


def _get_owned_notification(db: Session, user: User, notification_id: str) -> Notification:
    note = db.get(Notification, notification_id)
    if not note or note.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    if note.user_id and note.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return note


@router.patch("/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    notification_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NotificationOut:
    note = _get_owned_notification(db, user, notification_id)
    note.read = True
    db.commit()
    return note


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notification(
    notification_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    note = _get_owned_notification(db, user, notification_id)
    db.delete(note)
    db.commit()


@router.patch("/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_read(
    role: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    notes = _notification_query(db, user, role).filter(Notification.read.is_(False)).all()
    for n in notes:
        n.read = True
    db.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear_notifications(
    role: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    notes = _notification_query(db, user, role).all()
    for n in notes:
        db.delete(n)
    db.commit()
