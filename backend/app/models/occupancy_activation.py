from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


HANDOVER_EVENT_TYPES = ("HANDOVER_READY", "POSSESSION_DELIVERED", "RENTER_RECEIPT")
HANDOVER_ACTOR_KINDS = ("provider_admin", "renter_user")
ACTIVATION_OUTCOMES = ("ACTIVATE", "WAITING_FOR_GATE", "MANUAL_REVIEW", "BLOCKED")


class OccupancyHandoverEvent(Base):
    """Immutable, actor-attributed handover evidence for one occupancy."""

    __tablename__ = "occupancy_handover_events"
    __table_args__ = (
        UniqueConstraint("occupancy_id", "event_type", name="uq_occupancy_handover_event_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    occupancy_id: Mapped[int] = mapped_column(ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("user_accounts.id"), nullable=True)
    evidence_ref: Mapped[str] = mapped_column(String(1024), default="")
    notes: Mapped[str] = mapped_column(String(2000), default="")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    occupancy: Mapped["Occupancy"] = relationship()
    actor_admin: Mapped["AdminUser"] = relationship()
    actor_user: Mapped["UserAccount"] = relationship()


class OccupancyActivationDecision(Base):
    """Immutable, versioned result of evaluating the activation gate."""

    __tablename__ = "occupancy_activation_decisions"
    __table_args__ = (
        UniqueConstraint("occupancy_id", "decision_version", name="uq_occupancy_activation_decision_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    occupancy_id: Mapped[int] = mapped_column(ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=False, index=True)
    decision_version: Mapped[int] = mapped_column(Integer, nullable=False)
    gate_rule_version: Mapped[int] = mapped_column(Integer, default=1)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    reason_codes: Mapped[list] = mapped_column(JSON, default=list)
    checks: Mapped[dict] = mapped_column(JSON, default=dict)
    trigger: Mapped[str] = mapped_column(String(50), nullable=False)
    evaluating_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    occupancy: Mapped["Occupancy"] = relationship()
    evaluating_admin: Mapped["AdminUser"] = relationship()
