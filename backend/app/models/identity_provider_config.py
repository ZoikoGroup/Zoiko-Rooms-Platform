"""ZR-IDV-ADR-001 provider configuration that must not live in code:

- IdentityProviderReasonMapping: the provider's own reason codes (e.g.
  Veriff resubmission / decline codes) -> Zoiko safe reason codes. These
  depend on the contracted product, so they're data an admin maintains.
- IdentityGoLiveGate: the Section 17 production go-live gates (commercial
  terms, DPA, security review, ...), each confirmed by a named admin. While
  any gate is open a production integration stays switched off.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Section 17 -- go-live gates outside engineering, plus the automated
# connection test that stands for sandbox acceptance of the integration.
GO_LIVE_GATES: tuple[tuple[str, str], ...] = (
    ("COMMERCIAL_TERMS", "Commercial pricing and committed volume model approved"),
    ("PLAN_WEBHOOK_CONFIRMED", "Veriff plan confirmed to support the intended webhook contract"),
    ("DPA_SIGNED", "DPA, subprocessors and cross-border transfer mechanism reviewed"),
    ("SECURITY_REVIEW", "Security / penetration / compliance evidence reviewed"),
    ("RETENTION_DELETION_AGREED", "Retention and deletion responsibilities contractually understood"),
    ("LAUNCH_COUNTRIES_VALIDATED", "Launch-country document support validated against the Country Regulatory Pack"),
    ("SANDBOX_ACCEPTANCE", "Sandbox integration accepted (automated suite green, QA journeys passed)"),
    ("PRODUCTION_CREDENTIALS", "Separate production credentials provisioned in the secret store"),
    ("ESCALATION_CONTACTS", "Operational escalation path and vendor support contacts documented"),
)


class IdentityProviderReasonMapping(Base):
    __tablename__ = "identity_provider_reason_mappings"
    __table_args__ = (
        UniqueConstraint("provider_code", "provider_decision", "provider_reason_code", name="uq_identity_reason_mapping"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_code: Mapped[str] = mapped_column(String(50), nullable=False)
    # e.g. "resubmission_requested", "declined"
    provider_decision: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_reason_code: Mapped[str] = mapped_column(String(40), nullable=False)
    zoiko_reason_code: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class IdentityGoLiveGate(Base):
    __tablename__ = "identity_go_live_gates"
    __table_args__ = (UniqueConstraint("provider_code", "gate_code", name="uq_identity_go_live_gate"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_code: Mapped[str] = mapped_column(String(50), nullable=False)
    gate_code: Mapped[str] = mapped_column(String(40), nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confirmed_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Where the evidence lives (contract reference, ticket, document link) -- never the document itself.
    evidence_reference: Mapped[str] = mapped_column(String(500), default="")
    note: Mapped[str] = mapped_column(String(1000), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class IdentityProviderCheck(Base):
    """Result of an automated connection test against a provider integration."""

    __tablename__ = "identity_provider_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_code: Mapped[str] = mapped_column(String(50), nullable=False)
    integration_id: Mapped[str] = mapped_column(String(50), default="")
    check_type: Mapped[str] = mapped_column(String(40), default="CONNECTION_TEST")
    ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    detail: Mapped[str] = mapped_column(String(300), default="")
    run_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
