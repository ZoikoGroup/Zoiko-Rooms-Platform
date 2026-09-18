"""ZR-ENG-CLR-004 Section 13.1/AC-23/AC-24: signature_request ('Signer +
required method + deadline') plus the provider-callback ingestion machinery
that AC-23/24 need something real to test against.

No real DocuSign/Adobe Sign integration exists in this codebase -- same
honest-simulation status as SimulatedPayment in models/finance.py, whose own
docstring says it mirrors 'a real processor's create-intent/webhook-confirm
shape... so a real adapter can later replace only the confirmation step.'
SignatureProviderEvent/dispatch/callback-ingestion here follow that exact
established pattern: a real event ledger, real idempotent dedup, a real
outage flag that blocks (never silently downgrades) -- just driven by an
authenticated admin action standing in for the provider's own webhook call,
consistent with how this codebase already simulates every other external
provider (payments, e-signature itself)."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

SIGNATURE_REQUEST_STATUSES = ("PENDING", "DISPATCHED", "COMPLETED", "FAILED", "EXPIRED")


class SignatureRequest(Base):
    """One row per required signer, created when the agreement (or an
    amendment's new version) is sent -- see crud/signature_provider.py:
    create_signature_requests_for_agreement."""

    __tablename__ = "signature_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id", ondelete="CASCADE"), nullable=False)
    agreement_version_id: Mapped[int] = mapped_column(ForeignKey("agreement_versions.id", ondelete="CASCADE"), nullable=False)
    party_role: Mapped[str] = mapped_column(String(20), nullable=False)
    method: Mapped[str] = mapped_column(String(20), default="SIMPLE_ESIGN")
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_transaction_id: Mapped[str] = mapped_column(String(100), default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    agreement: Mapped["Agreement"] = relationship()


class SignatureProviderEvent(Base):
    """AC-23 'authenticated, replay-protected and idempotent': provider_event_id
    is unique at the DB level -- a retried/duplicated callback for the same
    event is rejected by IntegrityError and treated as a no-op, never
    reprocessed (see crud/signature_provider.py:ingest_provider_callback,
    same SAVEPOINT+IntegrityError idempotency idiom as
    services/inventory.py:create_hold)."""

    __tablename__ = "signature_provider_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_event_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    signature_request_id: Mapped[int | None] = mapped_column(ForeignKey("signature_requests.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SignatureProviderStatus(Base):
    """A single row (id=1) toggling the simulated provider's health -- AC-24
    'recover from provider outage without silently lowering required
    signature assurance': while unhealthy, dispatch_signature_request fails
    closed (503) rather than falling back to a weaker method, and
    reconcile_stalled_signature_requests marks anything stuck past its
    deadline FAILED for manual review instead of auto-completing it."""

    __tablename__ = "signature_provider_status"

    id: Mapped[int] = mapped_column(primary_key=True)
    healthy: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
