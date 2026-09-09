from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

APPLICATION_STATUSES = ("SUBMITTED", "WITHDRAWN", "DECIDED")
APPLICATION_DECISIONS = ("APPROVED", "REJECTED")
OFFER_STATUSES = ("DRAFT", "SENT", "ACCEPTED", "DECLINED", "EXPIRED", "WITHDRAWN")
# ZR-ENG-CLR-001 Rule 7: PAYMENT_IN_PROGRESS/PAYMENT_PENDING sit between both
# signatures landing and the agreement becoming terminally SIGNED -- SIGNED is
# reached only once every initial (rent+deposit) obligation clears (see
# crud/leasing.py:confirm_agreement_payment), never from signatures alone.
# PAYMENT_PENDING is reserved for a future async payment rail (10.3); nothing
# in this codebase sets it yet (SimulatedPayment resolves synchronously).
# ZR-ENG-CLR-004 AC-12/9.1: PARTIALLY_EXECUTED -- exactly one required
# signature recorded, never itself mistaken for SIGNED (see
# crud/leasing.py:_apply_signature). EXPIRED -- the signing deadline (the
# offer's own confirmation_expires_at, per 4.5: "the booking can remain in
# its time-limited confirmation state until the applicable signing deadline
# expires") passed before every signature was collected; preserved for audit,
# never deleted (see services/booking_expiry.py).
# AC-18: AMENDMENT_PENDING -- a fully SIGNED, executed agreement now has a
# new WORKING version awaiting fresh signatures because an amendment was
# approved (see crud/agreement_amendments.py:approve_amendment). Behaves
# exactly like SENT for signing purposes (_apply_signature/user_sign_agreement
# both accept it), kept as its own name so it's never confused with the
# original pre-execution flow in the UI/audit trail.
AGREEMENT_STATUSES = (
    "DRAFT", "SENT", "PARTIALLY_EXECUTED", "PAYMENT_IN_PROGRESS", "PAYMENT_PENDING", "SIGNED", "EXPIRED", "VOID",
    "AMENDMENT_PENDING",
)

# ZR-ENG-CLR-001 Rule 6/Section 9: risk tier from services/overlap.py, recorded
# on the Offer at acceptance time -- never a hard one-booking-per-account rule.
# NONE: no conflicting live commitment for this occupant. REVIEW: a genuine
# but tolerable overlap (e.g. short relocation) -- allowed, flagged for
# visibility. BLOCK: a near-exact duplicate-occupancy overlap -- requires an
# admin override reason to accept (see crud/leasing.py:set_offer_status).
OCCUPANT_RISK_TIERS = ("NONE", "REVIEW", "BLOCK")


class Application(Base):
    """A renter's application to a listing. Submission alone never creates a rent
    obligation -- that only happens once an Offer is accepted and an Agreement is
    signed, several stages later."""

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), nullable=False)
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id", ondelete="CASCADE"), nullable=False)
    # ZR-ENG-CLR-001 Rule 6: "Account holder/payer and Named Occupant are
    # distinct roles." None means the applying/paying Guest above IS the
    # occupant (the common case, unchanged from before this column existed);
    # set only when a different verified person will actually live there --
    # see occupant_guest_id below, which is what overlap evaluation actually
    # keys off.
    named_occupant_guest_id: Mapped[str | None] = mapped_column(
        ForeignKey("guests.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="SUBMITTED")
    message: Mapped[str] = mapped_column(String(2000), default="")
    desired_move_in: Mapped[date | None] = mapped_column(Date, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    listing: Mapped["Listing"] = relationship()
    guest: Mapped["Guest"] = relationship(foreign_keys=[guest_id])
    named_occupant: Mapped["Guest"] = relationship(foreign_keys=[named_occupant_guest_id])
    decisions: Mapped[list["ApplicationDecision"]] = relationship(back_populates="application", cascade="all, delete-orphan")
    offer: Mapped["Offer"] = relationship(back_populates="application", uselist=False)

    @property
    def occupant_guest_id(self) -> str:
        """The identity overlap must actually be evaluated against (9.1) --
        never merely the applying/paying account."""
        return self.named_occupant_guest_id or self.guest_id


class ApplicationDecision(Base):
    """Append-only decision log -- a reversal or override is a new row, never an
    overwrite of a prior decision."""

    __tablename__ = "application_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(50), default="")
    note: Mapped[str] = mapped_column(String(2000), default="")
    decided_by_admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    application: Mapped["Application"] = relationship(back_populates="decisions")
    decided_by: Mapped["AdminUser"] = relationship()


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), unique=True, nullable=False)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), nullable=False)
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # ZR-ENG-CLR-001 Rule 7: set together the moment the offer becomes
    # ACCEPTED (see crud/leasing.py:_accept_offer_and_hold_room). Both stay
    # None for an offer that never reached ACCEPTED. The renter-facing
    # countdown must read confirmation_expires_at directly, never derive it
    # client-side from accepted_at + a hardcoded duration (10.1: "User-facing
    # countdown must derive from the server-side expiry timestamp").
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ZR-ENG-CLR-001 Rule 6/Section 9: set by services/overlap.py the moment
    # this offer is accepted -- see OCCUPANT_RISK_TIERS above.
    occupant_risk_tier: Mapped[str] = mapped_column(String(20), default="NONE")
    occupant_risk_reason: Mapped[str] = mapped_column(String(500), default="")

    application: Mapped["Application"] = relationship(back_populates="offer")
    listing: Mapped["Listing"] = relationship()
    guest: Mapped["Guest"] = relationship()
    terms: Mapped[list["OfferTerms"]] = relationship(back_populates="offer", cascade="all, delete-orphan", order_by="OfferTerms.version")
    agreement: Mapped["Agreement"] = relationship(back_populates="offer", uselist=False)


class OfferTerms(Base):
    """Append-only versioned terms -- a new negotiation round adds a new version row,
    the previous one is never edited in place."""

    __tablename__ = "offer_terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_rent: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    deposit_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    term_months: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    offer: Mapped["Offer"] = relationship(back_populates="terms")


class Agreement(Base):
    __tablename__ = "agreements"

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id", ondelete="CASCADE"), unique=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    content_ref: Mapped[str] = mapped_column(String(1024), default="")
    signed_by_provider_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_by_renter_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signature_ref: Mapped[str] = mapped_column(String(255), default="")
    # ZR-ENG-CLR-001 Rule 7 (10.2): set the moment both signatures land and the
    # agreement enters PAYMENT_IN_PROGRESS (see crud/leasing.py:_apply_signature).
    # None otherwise -- mirrors Offer.confirmation_expires_at's own "server-side
    # expiry timestamp, not a client-derived one" requirement.
    payment_session_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    offer: Mapped["Offer"] = relationship(back_populates="agreement")
    obligations: Mapped[list["Obligation"]] = relationship(back_populates="agreement")
    versions: Mapped[list["AgreementVersion"]] = relationship(
        back_populates="agreement", cascade="all, delete-orphan", order_by="AgreementVersion.version_no"
    )
    disclosures: Mapped[list["DisclosureRequirement"]] = relationship(
        back_populates="agreement", cascade="all, delete-orphan"
    )
    parties: Mapped[list["AgreementParty"]] = relationship(back_populates="agreement", cascade="all, delete-orphan")
    amendments: Mapped[list["AgreementAmendment"]] = relationship(
        back_populates="agreement", cascade="all, delete-orphan", order_by="AgreementAmendment.created_at"
    )


# ZR-ENG-CLR-004 Section 9.2: WORKING -> FROZEN -> EXECUTED_IMMUTABLE (the
# spec's own HASHED/SIGNING sub-states are collapsed into FROZEN here -- this
# codebase signs synchronously in one request, not across an async provider
# session, so there's no observable gap between "hash computed" and "signing
# opened" worth a separate state).
# AC-06: SUPERSEDED -- a WORKING version closed out by a material term change
# before it was ever frozen/executed (see
# crud/leasing.py:_invalidate_pending_agreement_version). Terminal for that
# version; the agreement moves on to a new version_no, never back to this one.
AGREEMENT_VERSION_STATUSES = ("WORKING", "FROZEN", "EXECUTED_IMMUTABLE", "SUPERSEDED")


class AgreementVersion(Base):
    """ZR-ENG-CLR-004 Section 13.1/13.3: the immutable snapshot of every
    material fact (premises, parties, commercial terms, resolved agreement
    profile) used to generate this agreement -- read once at creation time,
    never re-read from live Listing/Offer/Guest rows again (AC-27). Created
    WORKING at crud/leasing.py:create_agreement; frozen (hash computed, PDF
    rendered exactly once, never regenerated) once execution completes -- see
    freeze_agreement_version."""

    __tablename__ = "agreement_versions"
    __table_args__ = (UniqueConstraint("agreement_id", "version_no", name="uq_agreement_version_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id", ondelete="CASCADE"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="WORKING")
    # Premises/parties/commercial-terms/resolved-profile facts, frozen at
    # generation time -- see services/agreement_profile.py and
    # crud/leasing.py:_build_agreement_snapshot.
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    # SHA-256 of the rendered PDF bytes -- set only once, when
    # freeze_agreement_version runs; None for a still-WORKING version.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agreement: Mapped["Agreement"] = relationship(back_populates="versions")
    artifact: Mapped["DocumentArtifact"] = relationship(back_populates="agreement_version", uselist=False)
    signature_events: Mapped[list["SignatureEvent"]] = relationship(back_populates="agreement_version")
    premises: Mapped["AgreementPremises"] = relationship(back_populates="agreement_version", uselist=False)
    commercial_terms: Mapped["CommercialTermsSnapshot"] = relationship(back_populates="agreement_version", uselist=False)
    execution_certificate: Mapped["ExecutionCertificate"] = relationship(back_populates="agreement_version", uselist=False)


class DocumentArtifact(Base):
    """ZR-ENG-CLR-004 Section 13.1/AC-08: the rendered, hash-verified PDF for
    one EXECUTED_IMMUTABLE AgreementVersion. One artifact per version, ever
    -- storage_ref is never reused/overwritten under the same id (see
    core/agreement_documents.py, same on-disk pattern as identity document
    uploads)."""

    __tablename__ = "document_artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_version_id: Mapped[int] = mapped_column(
        ForeignKey("agreement_versions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), default="application/pdf")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    agreement_version: Mapped["AgreementVersion"] = relationship(back_populates="artifact")


class SignatureEvent(Base):
    """ZR-ENG-CLR-004 Section 8.2: immutable per-signer evidence, recorded
    inside crud/leasing.py:_apply_signature alongside the existing
    signed_by_*_at timestamp write. method defaults to SIMPLE_ESIGN -- the
    honest description of this codebase's actual click-to-sign flow; no real
    e-signature provider is integrated, so ADVANCED/QUALIFIED/WITNESSED/
    NOTARIZED/WET_INK are declared as valid values (for a future provider)
    but never produced here."""

    __tablename__ = "signature_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id", ondelete="CASCADE"), nullable=False)
    agreement_version_id: Mapped[int] = mapped_column(ForeignKey("agreement_versions.id", ondelete="CASCADE"), nullable=False)
    signer_role: Mapped[str] = mapped_column(String(20), nullable=False)
    signer_identifier: Mapped[str] = mapped_column(String(50), nullable=False)
    method: Mapped[str] = mapped_column(String(20), default="SIMPLE_ESIGN")
    # The version's own snapshot hash is not yet meaningful at signing time
    # (the version isn't FROZEN/hashed until execution completes) -- this
    # column exists for a future assurance level that hashes at signing time
    # too; left blank by today's SIMPLE_ESIGN path. AC-11: WET_INK is the one
    # alternate method actually produced today (crud/leasing.py:
    # record_wet_ink_signature) -- document_hash there is the uploaded scan's
    # hash, not the agreement's.
    document_hash: Mapped[str] = mapped_column(String(64), default="")
    # AC-11: storage_ref for the uploaded scan backing a WET_INK signature --
    # blank for SIMPLE_ESIGN (there's no separate file, the click itself is
    # the evidence). Same on-disk convention as DocumentArtifact.storage_ref.
    evidence_storage_ref: Mapped[str] = mapped_column(String(255), default="")
    # AC-11: method-specific structured evidence -- consent_statement for
    # ACKNOWLEDGMENT, trust_service_certificate_ref for QUALIFIED_ESIGN,
    # witness_name/witness_contact for WITNESSED_ESIGN, notary_name/
    # notary_license_ref for NOTARIZED. See crud/leasing.py:_apply_signature's
    # METHOD_REQUIRED_EVIDENCE for exactly which keys each method requires.
    evidence_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    agreement_version: Mapped["AgreementVersion"] = relationship(back_populates="signature_events")
