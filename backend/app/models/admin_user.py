from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

ADMIN_ROLES = ("admin", "super_admin")
ADMIN_APPROVAL_STATUSES = ("pending", "approved", "rejected")
# ZR-ENG-CLR-010 Section 12/20: the named specialist roles the dispute spec
# calls for (Support/Dispute Officer/Finance/Trust & Safety/Legal-Compliance).
# Deliberately a SECOND, disputes-only field rather than an extension of
# ADMIN_ROLES above -- that two-value role gates dozens of unrelated domains
# platform-wide (finance, leasing, occupancy, listings...) via a hardcoded
# "!= super_admin" check at every call site; folding five new values into it
# would silently change every one of those checks' behavior for an admin who
# picks up a dispute specialization, none of which this build has audited.
# None (the default, and every admin that existed before this field did) is
# not "no access" -- see services/dispute_rbac.py's own docstring for why an
# unassigned admin keeps today's blanket dispute access rather than being
# retroactively locked out.
DISPUTE_ADMIN_ROLES = ("SUPPORT", "DISPUTE_OFFICER", "FINANCE", "TRUST_AND_SAFETY", "LEGAL_COMPLIANCE")

# ZR-PAY-LINK-003 Section 17 Permissions Matrix: the narrow "Staff" tier
# ("Controlled support only" / "Case-bound" / "Controlled + audited") for
# rental-payment confirm/correction actions -- deliberately a THIRD,
# payments-only field for the exact same reason dispute_role above is kept
# separate from `role`: folding this into ADMIN_ROLES would silently change
# every "role != super_admin" check across the whole admin surface, not just
# the rental-payment routes this is meant to loosen. None (the default)
# means "not payment support staff", not "no access" -- an admin without
# this flag still has whatever `role` alone already grants them.
PAYMENT_STAFF_ROLES = ("PAYMENT_SUPPORT",)


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
    # See DISPUTE_ADMIN_ROLES above -- null means "not yet specialized",
    # not "no dispute access".
    dispute_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # See PAYMENT_STAFF_ROLES above -- null means "not payment support staff".
    payment_staff_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    approval_status: Mapped[str] = mapped_column(String(20), default="approved")
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
