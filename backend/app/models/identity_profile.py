"""ZR-IDENTITY-001: account-level identity verification.

Identity = the natural person. One IdentityProfile per party (the account's
person), reused across every property; Owner / Agent / Tenant-subletter are
property-scoped relationships and never stored here (Section 2.2). Each
attempt to prove the identity is an IdentityVerification row (the session +
its evidence, models/identity_verification.py); the profile carries the
canonical, server-authoritative state every gate reads (Section 8.4).

IdentityRegulatoryPack is the Country Regulatory Pack (Sections 1.1, 11):
accepted documents, date-of-birth/age rules, consent and privacy wording,
retention and re-verification policy per country -- data, not code.
"""

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Section 7.1 -- the canonical identity state, shared by the account-level
# profile and each verification session.
IDENTITY_STATES = (
    "NOT_STARTED", "IN_PROGRESS", "PROCESSING", "PENDING_REVIEW",
    "ACTION_REQUIRED", "VERIFIED", "REVERIFICATION_REQUIRED", "FAILED",
)
# Section 2.3 -- internal assurance levels (not NIST IAL claims).
ASSURANCE_LEVELS = ("IV-0", "IV-1", "IV-2")
# Section 12.1 verification_method, normalized.
# Document + Selfie through the identity provider (Veriff) is the only method.
VERIFICATION_METHODS = ("DOCUMENT",)
# Section 8.2 normalized_outcome.
NORMALIZED_OUTCOMES = ("PASS", "REVIEW", "ACTION_REQUIRED", "FAIL")
# Section 3 -- the property role the person is verifying for. Routing
# context only (where to send them afterwards), never an identity attribute.
ROLE_CONTEXTS = ("OWNER", "AGENT", "SUBLETTER")
# Wildcard pack used when a country has no pack of its own.
DEFAULT_PACK_COUNTRY = "*"


class IdentityProfile(Base):
    __tablename__ = "identity_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(
        ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, unique=True, index=True,
    )
    state: Mapped[str] = mapped_column(String(30), default="NOT_STARTED", nullable=False)
    assurance_level: Mapped[str] = mapped_column(String(10), default="IV-0", nullable=False)

    # Section 5.2 -- legal name as given / middle / family (no forced Western
    # order; stored exactly as typed, diacritics and non-Latin scripts kept).
    given_name: Mapped[str] = mapped_column(String(200), default="")
    middle_names: Mapped[str] = mapped_column(String(200), default="")
    family_name: Mapped[str] = mapped_column(String(200), default="")
    # Only collected when the country pack requires it.
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    country_code: Mapped[str] = mapped_column(String(10), default="")

    verified_legal_name: Mapped[str] = mapped_column(String(600), default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Policy-driven (pack.reverification_interval_days); never document expiry (Section 7.2).
    reverification_required_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reverification_reason: Mapped[str] = mapped_column(String(60), default="")
    verification_method: Mapped[str] = mapped_column(String(30), default="")
    provider_code: Mapped[str] = mapped_column(String(50), default="")
    provider_subject_reference: Mapped[str] = mapped_column(String(255), default="")
    reason_codes: Mapped[list] = mapped_column(JSON, default=list)
    current_verification_id: Mapped[int | None] = mapped_column(
        ForeignKey("identity_verifications.id", ondelete="SET NULL", name="fk_identity_profiles_current_verification_id"),
        nullable=True,
    )

    # Section 12.1 optimistic concurrency.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __mapper_args__ = {"version_id_col": version}

    party: Mapped["Party"] = relationship()

    @property
    def legal_name(self) -> str:
        return " ".join(p for p in (self.given_name, self.middle_names, self.family_name) if p.strip())


class IdentityRegulatoryPack(Base):
    __tablename__ = "identity_regulatory_packs"
    __table_args__ = (UniqueConstraint("country_code", "version", name="uq_identity_pack_country_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # ISO 3166-1 alpha-2, or "*" for the global default.
    country_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    country_name: Mapped[str] = mapped_column(String(120), default="")
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Document types accepted as identity evidence here (values from
    # models/identity_verification.py IDENTITY_DOCUMENT_TYPES).
    accepted_document_types: Mapped[list] = mapped_column(JSON, default=list)
    # Methods offered here (VERIFICATION_METHODS).
    available_methods: Mapped[list] = mapped_column(JSON, default=list)
    date_of_birth_required: Mapped[bool] = mapped_column(Boolean, default=False)
    minimum_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Shown before any liveness/selfie capture where local law needs it (Section 5.5).
    biometric_consent_text: Mapped[str] = mapped_column(Text, default="")
    privacy_notice_text: Mapped[str] = mapped_column(Text, default="")
    # How long raw evidence is kept after a decision; None = keep until policy set.
    evidence_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Periodic renewal; None = no time-based re-verification (Section 7.3).
    reverification_interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reverify_on_account_recovery: Mapped[bool] = mapped_column(Boolean, default=False)
    # Which provider adapter handles DOCUMENT verifications here (Veriff).
    document_provider_code: Mapped[str] = mapped_column(String(50), default="veriff")
    # Server-enforced retry policy (ZR-IDV-ADR-001 Section 10 /restart).
    max_attempts_per_day: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class IdentityProviderEvent(Base):
    """Section 8.3/14 -- webhook ledger: one row per provider event id, so a
    replayed or duplicate callback is recognised and never reprocessed."""

    __tablename__ = "identity_provider_events"
    __table_args__ = (UniqueConstraint("provider_code", "provider_event_id", name="uq_identity_provider_event"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_code: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    identity_verification_id: Mapped[int | None] = mapped_column(
        ForeignKey("identity_verifications.id", ondelete="SET NULL"), nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(60), default="")
    # SHA-256 of the raw body (dedup fingerprint, audit).
    payload_sha256: Mapped[str] = mapped_column(String(64), default="")
    # The normalized event, Fernet-encrypted, held only until it is processed
    # (durable acceptance before acknowledging -- ZR-IDV-ADR-001 Section 8).
    payload_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # accepted -> processed / ignored / failed; "rejected" = failed authentication.
    status: Mapped[str] = mapped_column(String(20), default="accepted", nullable=False)
    processing_error: Mapped[str] = mapped_column(String(300), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
