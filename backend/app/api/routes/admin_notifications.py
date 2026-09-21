from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.crud import notification as crud
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.schemas.notification import NotificationPreferenceRead, NotificationPreferenceUpdate, NotificationRead, UnreadCountRead

# Shared by both "admin" and "super_admin" roles -- every query below is scoped to
# the caller's own admin.id, so a regular admin can never see another admin's rows
# and, since only super admins are ever fanned out super-admin-only notifications
# (see crud.notify_all_super_admins), a regular admin simply has none of those rows
# to begin with. No role check is needed here beyond "is a logged-in admin."
router = APIRouter(prefix="/api/notifications", tags=["admin-notifications"], dependencies=[Depends(get_current_admin)])


@router.get("", response_model=list[NotificationRead])
def list_notifications(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return crud.list_for_admin(db, admin.id)


@router.get("/unread-count", response_model=UnreadCountRead)
def get_unread_count(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return UnreadCountRead(count=crud.count_unread_for_admin(db, admin.id))


@router.patch("/{notification_id}/read", response_model=NotificationRead)
def mark_read(notification_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    notification = crud.mark_read_for_admin(db, notification_id, admin.id)
    if not notification:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    return notification


@router.patch("/read-all")
def mark_all_read(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    updated = crud.mark_all_read_for_admin(db, admin.id)
    return {"updated": updated}


@router.get("/preferences", response_model=NotificationPreferenceRead)
def get_preferences(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return crud.get_or_create_preference_for_admin(db, admin.id)


@router.put("/preferences", response_model=NotificationPreferenceRead)
def put_preferences(
    payload: NotificationPreferenceUpdate, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    pref = crud.get_or_create_preference_for_admin(db, admin.id)
    return crud.update_preference(
        db, pref, opted_out_categories=payload.opted_out_categories, quiet_hours_enabled=payload.quiet_hours_enabled,
        quiet_hours_start_minute=payload.quiet_hours_start_minute, quiet_hours_end_minute=payload.quiet_hours_end_minute,
    )
