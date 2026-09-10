from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Transactional outbox scaffold -- rows are persisted after commit; async dispatch to
# real consumers is a later phase, not implemented here.
DELIVERY_STATES = ("pending", "delivered", "failed")


class DomainEvent(Base):
    """ZR-ENG-CLR-001 Section 11.3 / 12.2: the Audit/Event Layer must carry
    correlation and idempotency IDs, not just the event fact itself.
    correlation_id ties this event back to the request/audit trail that
    caused it (app/core/correlation.py); idempotency_key, when a caller
    supplies one, makes emit_event() itself idempotent -- a second emit with
    the same key returns the original row instead of creating a duplicate
    (see crud/events.py). Both are optional: most emit_event call sites
    don't supply either, and that's fine -- retrofitting every existing
    caller isn't required for either field to be useful where it matters."""

    __tablename__ = "domain_events"
    __table_args__ = (
        Index(
            "uq_domain_events_idempotency_key", "idempotency_key", unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    delivery_state: Mapped[str] = mapped_column(String(20), default="pending")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
