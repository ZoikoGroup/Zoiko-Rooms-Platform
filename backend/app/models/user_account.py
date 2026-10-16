from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserAccount(Base):
    """
    Regular user account for renters/providers.
    Links to Party for organizational relationships and role management.
    Separate from AdminUser which handles admin/super_admin access.
    """

    __tablename__ = "user_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(50), default="")
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id", ondelete="SET NULL"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # Set only when a password is changed via the forgot-password/reset-password
    # flow (not the authenticated change-password endpoint) -- see
    # app/api/deps.py:get_current_user, which rejects any token issued before this
    # timestamp so a reset immediately invalidates sessions from other devices.
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "Sign out of other sessions" (and the authenticated password change)
    # stamp this, truncated to the second; get_current_user rejects any token
    # issued in an earlier second. The caller's own session gets a fresh token.
    sessions_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Profile photo, in stored_files (category "avatar"). avatar_updated_at
    # doubles as a cache-buster for the image URL.
    avatar_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Self-service account closure: is_active goes false; records are kept for
    # their legal retention periods, not deleted.
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closure_reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    party: Mapped["Party"] = relationship(back_populates="user_accounts")

    @property
    def has_avatar(self) -> bool:
        return bool(self.avatar_ref)
