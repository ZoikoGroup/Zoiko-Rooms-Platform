from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditEvent(Base):
    """Append-only audit trail. Never updated or deleted by application code."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(30), default="")
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), default="")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    # ZR-ENG-CLR-001 Section 15: "capture... before/after state, object version...
    # and policy/ruleset version." Nullable -- most existing call sites (chat,
    # finance, identity verification, etc.) don't have a meaningful state
    # transition or policy version to report and are left as None rather than
    # forced to invent one.
    before_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    after_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    object_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
