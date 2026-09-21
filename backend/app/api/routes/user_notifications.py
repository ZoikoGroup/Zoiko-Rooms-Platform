from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.crud import notification as crud
from app.db.session import get_db
from app.models.user_account import UserAccount
from app.schemas.notification import NotificationPreferenceRead, NotificationPreferenceUpdate, NotificationRead, UnreadCountRead

router = APIRouter(prefix="/api/users/notifications", tags=["user-notifications"])


@router.get("", response_model=list[NotificationRead])
def list_notifications(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """Only ever the authenticated user's own notifications -- filtered by
    recipient_user_id on the backend, never trusted from the client."""
    return crud.list_for_user(db, user.id)


@router.get("/unread-count", response_model=UnreadCountRead)
def get_unread_count(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return UnreadCountRead(count=crud.count_unread_for_user(db, user.id))


@router.patch("/{notification_id}/read", response_model=NotificationRead)
def mark_read(notification_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    notification = crud.mark_read_for_user(db, notification_id, user.id)
    if not notification:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    return notification


@router.patch("/read-all")
def mark_all_read(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    updated = crud.mark_all_read_for_user(db, user.id)
    return {"updated": updated}


@router.get("/preferences", response_model=NotificationPreferenceRead)
def get_preferences(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """Section 11 gap: category opt-out + quiet hours -- previously no
    recipient had any way to shape which notifications they receive."""
    return crud.get_or_create_preference_for_user(db, user.id)


@router.put("/preferences", response_model=NotificationPreferenceRead)
def put_preferences(
    payload: NotificationPreferenceUpdate, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    pref = crud.get_or_create_preference_for_user(db, user.id)
    return crud.update_preference(
        db, pref, opted_out_categories=payload.opted_out_categories, quiet_hours_enabled=payload.quiet_hours_enabled,
        quiet_hours_start_minute=payload.quiet_hours_start_minute, quiet_hours_end_minute=payload.quiet_hours_end_minute,
    )
