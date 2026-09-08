from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

ADMIN_ROLES = ("admin", "super_admin")
ADMIN_APPROVAL_STATUSES = ("pending", "approved", "rejected")


class AdminUser(Base):
    __tablename__ = "admin_users"
    # 0001_initial.py created a table-level UNIQUE constraint on email plus a
    # separately named plain index (not a combined unique index) -- declared
    # explicitly here, matching the two real objects in the database, so
    # `alembic check` doesn't report a phantom constraint-vs-index diff.
    __table_args__ = (
        UniqueConstraint("email", name="admin_users_email_key"),
        Index("ix_admin_users_email", "email"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), default="Zoiko Admin")
    phone: Mapped[str] = mapped_column(String(50), default="")
    role: Mapped[str] = mapped_column(String(20), default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    approval_status: Mapped[str] = mapped_column(String(20), default="approved")
    # Login throttling: incremented on each failed login, reset on success.
    # locked_until blocks login attempts (regardless of password correctness)
    # until it elapses -- see crud/admin.py:authenticate.
    failed_login_attempts: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    settings: Mapped["AdminSettings"] = relationship(back_populates="admin_user", uselist=False, cascade="all, delete-orphan")
    listings: Mapped[list["Listing"]] = relationship(back_populates="owner")


class AdminSettings(Base):
    __tablename__ = "admin_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), unique=True)
    logo_url: Mapped[str] = mapped_column(String(1024), default="")
    notify_new_booking: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_payments: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_reviews: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_marketing: Mapped[bool] = mapped_column(Boolean, default=False)

    admin_user: Mapped["AdminUser"] = relationship(back_populates="settings")
