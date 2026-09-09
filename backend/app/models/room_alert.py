from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RoomAlert(Base):
    """An anonymous visitor's request to be emailed when a new room matching
    their criteria is published. Reached from the public marketing site --
    email is the identifier since there's no login at that point, not party_id."""

    __tablename__ = "room_alerts"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    city: Mapped[str] = mapped_column(String(255), nullable=False)
    min_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    room_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Stored in plaintext, unlike a password-reset token: this needs to be
    # reusable in every future match-notification email for this alert's
    # lifetime, not single-use, and the worst case of it leaking is someone
    # unsubscribing an alert that isn't theirs -- not a security breach.
    unsubscribe_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
