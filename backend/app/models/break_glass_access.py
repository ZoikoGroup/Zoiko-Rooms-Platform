from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-ENG-CLR-012 AC-27: "Break-glass sensitive-data access is time-limited,
# reason-coded and audited." Document downloads already require super_admin
# (the codebase's highest role) -- this is for the OTHER real case the doc
# means: a plain admin who normally cannot see raw evidence, but has a
# genuine one-off reason to (an active dispute, a support escalation), gets
# a short, self-service, fully audited window rather than either being
# permanently promoted to super_admin or blocked outright.
BREAK_GLASS_DEFAULT_DURATION_MINUTES = 15


class BreakGlassAccessGrant(Base):
    __tablename__ = "break_glass_access_grants"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False, index=True)
    related_entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    related_entity_id: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    admin: Mapped["AdminUser"] = relationship()
