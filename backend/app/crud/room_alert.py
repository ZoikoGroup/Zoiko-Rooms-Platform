import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.ids import new_id
from app.models.room_alert import RoomAlert
from app.schemas.room_alert import RoomAlertCreate


def create_alert(db: Session, data: RoomAlertCreate) -> RoomAlert:
    alert = RoomAlert(
        id=new_id("ALERT"),
        email=data.email.strip().lower(),
        city=data.city.strip(),
        min_price=data.min_price,
        max_price=data.max_price,
        room_type=data.room_type.strip() if data.room_type else None,
        unsubscribe_token=secrets.token_urlsafe(24),
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


def unsubscribe_alert(db: Session, alert_id: str, token: str) -> bool:
    """Deactivates an alert if the token matches. Returns False for any
    invalid alert_id/token combination without distinguishing why."""
    alert = db.get(RoomAlert, alert_id)
    if not alert or alert.unsubscribe_token != token:
        return False
    alert.is_active = False
    db.commit()
    return True


def list_active_alerts(db: Session) -> list[RoomAlert]:
    return list(db.scalars(select(RoomAlert).where(RoomAlert.is_active.is_(True))))


def mark_notified(db: Session, alert: RoomAlert, when: datetime) -> None:
    alert.last_notified_at = when
    db.commit()
