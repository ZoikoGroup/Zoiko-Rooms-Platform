"""ZR-AUTHORITY-002 -- Authority Verification.

A property-scoped proof that a verified person (optionally acting for a
verified organization) may advertise a specific property or room. Authority
is a relationship record per person x property x role (Section 2.2), never
an account type. Owner, Agent / Property Manager and Tenant / Subletter use
different evidence chains (Sections 5-7) driven by the Authority Regulatory
Pack (Section 4). Identity, property existence and listing authority remain
independent server-authoritative states."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Section 9 state machine (EXPIRING_SOON is derived from expires_at).
AUTHORITY_STATES = (
    "COLLECTING", "SUBMITTED", "MANUAL_REVIEW", "ACTION_REQUIRED", "VERIFIED", "EXPIRED", "REVOKED", "REJECTED",
    "SUPERSEDED",
)
ACTIVE_STATES = ("COLLECTING", "SUBMITTED", "MANUAL_REVIEW", "ACTION_REQUIRED", "VERIFIED")
# Section 12.1 relationship_type.
RELATIONSHIP_TYPES = ("OWNER", "CO_OWNER", "REPRESENTATIVE", "AGENT", "PROPERTY_MANAGER", "TENANT_SUBLETTER")
# Which route (evidence chain) each relationship follows.
ROUTE_FOR_RELATIONSHIP = {
    "OWNER": "OWNER", "CO_OWNER": "OWNER", "REPRESENTATIVE": "OWNER",
    "AGENT": "AGENT", "PROPERTY_MANAGER": "AGENT", "TENANT_SUBLETTER": "SUBLET",
}
# Section 2.3 assurance levels.
ASSURANCE_LEVELS = ("AV-0", "AV-1", "AV-2", "AV-X")
# Section 12.1 authority_scope_codes.
SCOPE_CODES = ("ADVERTISE", "RENT", "MANAGE", "SUBLET", "COLLECT_RENT")
# Section 4.1 evidence classes.
EVIDENCE_CLASSES = ("PROPERTY_RIGHT", "MANDATE", "OCCUPATION_RIGHT", "SUBLET_PERMISSION", "ORGANIZATION_LINK",
                    "SOURCE_ASSERTION")
SOURCE_TYPES = ("UPLOAD", "REGISTRY", "OWNER_CONFIRMATION", "CONNECTOR")
EVIDENCE_PROCESSING = ("PROCESSING", "READY", "NEEDS_REPLACEMENT", "REPLACED")
CONFIRMATION_KINDS = ("MANDATE", "SUBLET_PERMISSION", "CO_OWNER_CONSENT")
REVIEW_DECISIONS = ("APPROVE", "REQUEST_EVIDENCE", "REJECT")


class AuthorityRegulatoryPack(Base):
    """Section 4: per-country authority requirements, terminology and rules.
    `requirements` maps a route (OWNER / AGENT / SUBLET) to its requirement
    definitions: [{requirement_id, evidence_class, title, purpose,
    accepted_examples[], required, owner_confirmation}]. The frontend renders
    these -- it never hard-codes legal evidence lists (Section 15.3)."""

    __tablename__ = "authority_regulatory_packs"

    id: Mapped[int] = mapped_column(primary_key=True)
    country_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    country_name: Mapped[str] = mapped_column(String(120), default="")
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requirements: Mapped[dict] = mapped_column(JSON, default=dict)
    # Localized terms, e.g. {"tenant": "tenant", "mandate": "management agreement"}.
    terminology: Mapped[dict] = mapped_column(JSON, default=dict)
    sublet_consent_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    co_owner_consent_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Identity must be verified before authority intake unless this is true (Section 3.2).
    parallel_identity_intake: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_validity_days: Mapped[int] = mapped_column(Integer, default=365, nullable=False)
    expiring_soon_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    evidence_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # What happens to live listings when authority expires / is revoked.
    listing_control: Mapped[str] = mapped_column(String(20), default="SUSPEND", nullable=False)  # SUSPEND | NONE
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Organization(Base):
    """Minimal organization record for agents acting through a company
    (Section 6.1). Verified once by Trust & Safety and reused across that
    representative's properties. An organization never counts as a
    property mandate."""

    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_by_party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    registration_number: Mapped[str] = mapped_column(String(100), default="")
    country_code: Mapped[str] = mapped_column(String(10), default="")
    representative_role: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)  # PENDING | VERIFIED | REJECTED
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AuthorityVerification(Base):
    """Section 12.1 minimum authority record."""

    __tablename__ = "authority_verifications"
    __table_args__ = (UniqueConstraint("party_id", "idempotency_key", name="uq_authority_party_idempotency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    # Empty = the whole property where the pack allows; otherwise specific rooms.
    room_scope_ids: Mapped[list] = mapped_column(JSON, default=list)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True)
    account_user_id: Mapped[int | None] = mapped_column(ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True)
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)
    relationship_type: Mapped[str] = mapped_column(String(20), nullable=False)
    acting_capacity: Mapped[str] = mapped_column(String(20), default="PERSONAL")  # PERSONAL | ORGANIZATION
    country_code: Mapped[str] = mapped_column(String(10), default="")
    pack_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(20), default="COLLECTING", nullable=False, index=True)
    assurance_level: Mapped[str] = mapped_column(String(5), default="AV-0", nullable=False)

    # Principal (owner / landlord / entity). Name kept encrypted for the
    # reviewer; the hash supports matching without decrypting.
    principal_name_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    principal_name_hash: Mapped[str] = mapped_column(String(64), default="")
    principal_party_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    scope_codes: Mapped[list] = mapped_column(JSON, default=list)
    restrictions: Mapped[str] = mapped_column(String(1000), default="")
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revocation_reason_code: Mapped[str] = mapped_column(String(40), default="")

    # Section 4.3 match model.
    match_results: Mapped[dict] = mapped_column(JSON, default=dict)
    reason_codes: Mapped[list] = mapped_column(JSON, default=list)
    review_reason_codes: Mapped[list] = mapped_column(JSON, default=list)

    attested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expiring_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    reviewer_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    first_approver_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    review_note: Mapped[str] = mapped_column(String(1000), default="")
    # Renewal / reconsideration chain (Section 9.2: never overwrite history).
    previous_id: Mapped[int | None] = mapped_column(ForeignKey("authority_verifications.id"), nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_reconsideration: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reconsideration_note: Mapped[str] = mapped_column(String(1000), default="")

    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    submit_idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    property: Mapped["Property"] = relationship()
    organization: Mapped[Organization | None] = relationship()
    evidence: Mapped[list["AuthorityEvidence"]] = relationship(
        back_populates="verification", cascade="all, delete-orphan", order_by="AuthorityEvidence.id",
    )
    confirmations: Mapped[list["AuthorityConfirmation"]] = relationship(
        back_populates="verification", cascade="all, delete-orphan", order_by="AuthorityConfirmation.id",
    )


class AuthorityEvidence(Base):
    """Section 12.1 evidence_items[]. Uploads are encrypted at rest; owner
    confirmations are recorded as evidence with source_type OWNER_CONFIRMATION."""

    __tablename__ = "authority_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    verification_id: Mapped[int] = mapped_column(ForeignKey("authority_verifications.id", ondelete="CASCADE"), nullable=False, index=True)
    requirement_id: Mapped[str] = mapped_column(String(60), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(30), nullable=False)  # evidence class
    source_type: Mapped[str] = mapped_column(String(20), default="UPLOAD", nullable=False)
    issuer: Mapped[str] = mapped_column(String(200), default="")
    document_reference: Mapped[str] = mapped_column(String(200), default="")
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stored_filename: Mapped[str | None] = mapped_column(String(300), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    content_type: Mapped[str] = mapped_column(String(100), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    file_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    processing_status: Mapped[str] = mapped_column(String(20), default="PROCESSING", nullable=False)
    scan_status: Mapped[str] = mapped_column(String(20), default="NOT_SCANNED")
    readable: Mapped[bool] = mapped_column(Boolean, default=False)
    property_matched: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    name_matched: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # representative / owner name
    principal_matched: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reused_elsewhere: Mapped[bool] = mapped_column(Boolean, default=False)
    tamper_signal: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    replaced_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    verification: Mapped[AuthorityVerification] = relationship(back_populates="evidence")


class AuthorityConfirmation(Base):
    """A secure owner / landlord / co-owner confirmation request (Sections
    6.2 step 7, 7.3, 13): a short-lived link plus a one-time code sent to the
    same address. Only hashes of the token and code are stored; documents
    never appear in the email."""

    __tablename__ = "authority_confirmations"

    id: Mapped[int] = mapped_column(primary_key=True)
    verification_id: Mapped[int] = mapped_column(ForeignKey("authority_verifications.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    requirement_id: Mapped[str] = mapped_column(String(60), default="")
    recipient_email_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_email_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    recipient_name: Mapped[str] = mapped_column(String(200), default="")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)  # PENDING | CONFIRMED | DECLINED | EXPIRED
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responder_name: Mapped[str] = mapped_column(String(200), default="")
    responder_ip_hash: Mapped[str] = mapped_column(String(64), default="")
    responder_agent: Mapped[str] = mapped_column(String(200), default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    verification: Mapped[AuthorityVerification] = relationship(back_populates="confirmations")
