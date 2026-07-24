import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_roles
from app.core.pagination import PaginatedOut, paginate_query
from app.core.routing import CamelCaseAPIRoute
from app.models.notification import Notification
from app.models.user import User
from app.schemas import NotificationCreate, NotificationOut

router = APIRouter(prefix="/notifications", tags=["notifications"], route_class=CamelCaseAPIRoute)


@router.get("", response_model=PaginatedOut[NotificationOut] | list[NotificationOut])
def list_notifications(
    role: str | None = Query(None),
    page: int | None = Query(None, ge=1),
    limit: int | None = Query(None, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PaginatedOut[NotificationOut] | list[NotificationOut]:
    q = db.query(Notification).filter(Notification.institution_id == user.institution_id)
    if role:
        q = q.filter(Notification.role == role)
    q = q.order_by(Notification.created_at.desc())
    if page is None and limit is None:
        return q.all()
    items, total, page_n, limit_n, pages = paginate_query(q, page or 1, limit)
    return PaginatedOut(items=items, total=total, page=page_n, limit=limit_n, pages=pages)


@router.post("", response_model=NotificationOut, status_code=status.HTTP_201_CREATED)
def create_notification(
    body: NotificationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("tutor", "admin")),
) -> NotificationOut:
    note = Notification(
        id=f"n-{uuid.uuid4().hex[:8]}",
        institution_id=user.institution_id,
        role=body.role,
        kind=body.kind,
        title=body.title,
        message=body.message,
        created_at=datetime.now(timezone.utc).isoformat(),
        read=False,
        href=body.href,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.patch("/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    notification_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NotificationOut:
    note = db.get(Notification, notification_id)
    if not note or note.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    note.read = True
    db.commit()
    return note


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notification(
    notification_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    note = db.get(Notification, notification_id)
    if not note or note.institution_id != user.institution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    db.delete(note)
    db.commit()


@router.patch("/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_read(
    role: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    notes = (
        db.query(Notification)
        .filter(Notification.institution_id == user.institution_id, Notification.role == role)
        .all()
    )
    for n in notes:
        n.read = True
    db.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear_notifications(
    role: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    db.query(Notification).filter(
        Notification.institution_id == user.institution_id,
        Notification.role == role,
    ).delete()
    db.commit()
