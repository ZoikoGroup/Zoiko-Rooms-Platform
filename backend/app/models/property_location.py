"""ZR-PROPERTY-VERIFY-001 -- property & location verification.

Property-level verification sessions (one property, many rooms), their
evidence, and the per-country Property Regulatory Pack that drives required
address fields, accepted evidence, pin-movement thresholds, unit rules and
validity. The canonical structured address and private coordinates live on
models/property.py:Property; a session records how they were established
(entry mode, validation, geocode, pin confirmation) and the existence
decision. Address/location confirmation alone never produces VERIFIED
(Section 2 / P0 #2) -- only existence evidence, a source check or a reviewer
does."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Section 7 -- host-facing state. EXPIRING_SOON is derived from expires_at.
PROPERTY_VERIFICATION_STATES = (
    "NOT_STARTED", "IN_PROGRESS", "MANUAL_REVIEW", "ACTION_REQUIRED", "VERIFIED", "EXPIRED", "REJECTED", "INVALIDATED",
)
# Section 7.1 substates.
ADDRESS_STATUSES = ("VALIDATED", "PARTIAL", "UNRESOLVED")
GEOCODE_STATUSES = ("RESOLVED", "AMBIGUOUS", "NOT_FOUND")
LOCATION_PRECISIONS = ("ROOFTOP", "PARCEL", "INTERPOLATED", "STREET", "APPROXIMATE")
PIN_STATUSES = ("AUTO_CONFIRMED", "USER_CONFIRMED", "ADJUSTED", "REVIEW_REQUIRED")
EXISTENCE_STATUSES = ("SOURCE_CONFIRMED", "EVIDENCE_CONFIRMED", "MANUAL_CONFIRMED", "INSUFFICIENT")
ENTRY_MODES = ("SELECTED", "MANUAL")
PROPERTY_KINDS = ("HOUSE", "APARTMENT", "OTHER")
# Section 9 evidence classes -- exactly the spec's list (manual reviewer
# corroboration is a review outcome, not an upload type).
EVIDENCE_TYPES = (
    "LAND_REGISTRY_RECORD", "PROPERTY_TAX_RECORD", "BUILDING_UNIT_RECORD", "TITLE_DEED",
    "MORTGAGE_INSURANCE_STATEMENT", "UTILITY_BILL",
)
# Evidence that can establish existence on its own when it matches the
# address. A utility bill only supports an address association (Section 6.6
# evidence doctrine) -- never sufficient by itself.
SUPPLEMENTARY_EVIDENCE_TYPES = ("UTILITY_BILL", "MORTGAGE_INSURANCE_STATEMENT")
REVIEW_DECISIONS = ("APPROVE", "REJECT", "REQUEST_INFO")


class PropertyRegulatoryPack(Base):
    """Per-country property verification policy (Section 9 / 1.1). Versioned:
    an edit deactivates the row and inserts version + 1."""

    __tablename__ = "property_regulatory_packs"

    id: Mapped[int] = mapped_column(primary_key=True)
    country_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # ISO2 or "*"
    country_name: Mapped[str] = mapped_column(String(120), default="")
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Structured address components that must be present.
    required_address_fields: Mapped[list] = mapped_column(JSON, default=list)
    accepted_evidence_types: Mapped[list] = mapped_column(JSON, default=list)
    # Property kinds for which a unit / flat number is required.
    unit_required_for: Mapped[list] = mapped_column(JSON, default=list)
    # A pin moved further than this from the provider result needs review.
    pin_move_review_meters: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    # Public listing coordinate precision (decimal places; 2 ~ 1.1 km).
    public_location_decimals: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    validity_days: Mapped[int] = mapped_column(Integer, default=365, nullable=False)
    expiring_soon_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    evidence_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PropertyLocationVerification(Base):
    """One property verification session (Section 5 flow, Section 12 contract)."""

    __tablename__ = "property_location_verifications"
    __table_args__ = (UniqueConstraint("party_id", "idempotency_key", name="uq_plv_party_idempotency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="IN_PROGRESS", nullable=False, index=True)
    country_code: Mapped[str] = mapped_column(String(10), default="")
    pack_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Address step (Sections 6.2-6.3).
    entry_mode: Mapped[str] = mapped_column(String(10), default="")
    submitted_address: Mapped[dict] = mapped_column(JSON, default=dict)  # what the host entered/selected
    canonical_address: Mapped[dict] = mapped_column(JSON, default=dict)  # standardized components
    suggested_address: Mapped[dict] = mapped_column(JSON, default=dict)  # provider correction, if any
    address_status: Mapped[str] = mapped_column(String(12), default="")
    address_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Location step (Section 6.4). The provider's original result is kept
    # forever; an adjusted pin never overwrites it (Section 7.1).
    geocode_status: Mapped[str] = mapped_column(String(12), default="")
    location_precision: Mapped[str] = mapped_column(String(14), default="")
    original_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    confirmed_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    confirmed_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    pin_status: Mapped[str] = mapped_column(String(16), default="")
    pin_moved_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    pin_reverse_geocode: Mapped[str] = mapped_column(String(500), default="")
    pin_adjust_reason: Mapped[str] = mapped_column(String(300), default="")
    pin_adjust_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Unit step (Section 6.5).
    property_kind: Mapped[str] = mapped_column(String(12), default="")
    building_name: Mapped[str] = mapped_column(String(200), default="")
    unit: Mapped[str] = mapped_column(String(50), default="")
    floor: Mapped[str] = mapped_column(String(20), default="")
    address_fingerprint: Mapped[str] = mapped_column(String(64), default="", index=True)
    duplicate_of_property_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Screen 4 "Existing Zoiko property match found?" -- the host's answer
    # (NOT_SAME_PROPERTY | SAME_PROPERTY) and note, shown to the reviewer.
    duplicate_host_answer: Mapped[str] = mapped_column(String(20), default="", server_default="")
    duplicate_host_note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    # Section 6.4: device/session metadata recorded with each pin adjustment
    # (hashed network address + user agent; never raw IPs).
    pin_adjust_device: Mapped[dict] = mapped_column(JSON, default=dict)

    # Provider reference (Section 12.1 provider_refs; storage class per terms).
    provider: Mapped[str] = mapped_column(String(20), default="")
    provider_place_id: Mapped[str] = mapped_column(String(300), default="")
    provider_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Decision (Sections 7-8).
    existence_status: Mapped[str] = mapped_column(String(20), default="")
    source_check: Mapped[str] = mapped_column(String(20), default="UNAVAILABLE")  # registry/source check result
    reason_codes: Mapped[list] = mapped_column(JSON, default=list)
    attested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Reviewer (Section 16). Four-eyes: a duplicate case approved by one
    # reviewer waits for a second, different reviewer.
    reviewer_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    first_approver_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    review_reason_code: Mapped[str] = mapped_column(String(40), default="")
    review_note: Mapped[str] = mapped_column(String(1000), default="")

    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    submit_idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Optimistic concurrency (Section 13.3): every write bumps it; writers
    # send the version they read (If-Match) and get 409 when stale.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    property: Mapped["Property"] = relationship()
    evidence: Mapped[list["PropertyLocationEvidence"]] = relationship(
        back_populates="verification", cascade="all, delete-orphan", order_by="PropertyLocationEvidence.id",
    )


class PropertyLocationEvidence(Base):
    """An uploaded property-existence document (Section 6.6). Encrypted at
    rest; only its hash, type and the address-match result are kept in the
    database."""

    __tablename__ = "property_location_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    verification_id: Mapped[int] = mapped_column(
        ForeignKey("property_location_verifications.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    evidence_type: Mapped[str] = mapped_column(String(40), nullable=False)
    stored_filename: Mapped[str | None] = mapped_column(String(300), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    content_type: Mapped[str] = mapped_column(String(100), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    # NOT_SCANNED until a malware-scanning provider is configured.
    scan_status: Mapped[str] = mapped_column(String(20), default="NOT_SCANNED")
    readable: Mapped[bool] = mapped_column(Boolean, default=False)
    address_matched: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    unit_matched: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reused_elsewhere: Mapped[bool] = mapped_column(Boolean, default=False)
    # Document reading (services/property_document_analysis.py): where the
    # text came from, OCR quality and the extracted signals. The raw text is
    # never stored.
    text_source: Mapped[str] = mapped_column(String(12), default="NONE", server_default="NONE")
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality: Mapped[str] = mapped_column(String(20), default="UNREADABLE", server_default="UNREADABLE")
    postal_matched: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    owner_name_matched: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    document_type_matched: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    document_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_number: Mapped[str] = mapped_column(String(40), default="", server_default="")
    signals: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    verification: Mapped[PropertyLocationVerification] = relationship(back_populates="evidence")
