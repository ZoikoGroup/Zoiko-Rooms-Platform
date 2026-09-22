"""ZR-PAY-002 Section 2/6/12.1: the rental payment record domain -- Zoiko
Rooms provides obligations, instructions, tenant declarations, recipient
confirmations, disputes and an auditable history, but never collects, holds
or moves rent/deposit money itself. Kept fully independent of
models/finance.py's Obligation/SimulatedPayment/PayoutRecord/LedgerEntry
(Section 12.1's architecture rule) -- these tables record facts *about* a
rent/deposit payment (declared, confirmed, disputed), never the payment
itself.

RentalPaymentObligation intentionally mirrors models/finance.py:Obligation's
own amount/currency/due_date/agreement-or-occupancy linkage, and is created
alongside it (crud/leasing.py:create_agreement,
crud/occupancy.py:generate_next_rent_obligation) rather than replacing it --
this is a deliberate, temporary duplication while the two domains coexist;
reconciling or retiring the custody-based Obligation is a separate future
decision (see this repo's ZR-PAY-002 gap analysis), not something this
module does on its own."""

from datetime import date, datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.services.deposit_terminology import resolve_deposit_terminology

RENTAL_PAYMENT_OBLIGATION_TYPES = ("RENT", "DEPOSIT", "OTHER")

# ZR-PAY-LINK-003 Section 16: an obligation starts in one of the first two
# states and, once a RentalPaymentRecord exists against it, reflects that
# record's own state instead. RentalPaymentObligation.status and
# RentalPaymentRecord.status both draw from this same tuple (the spec's own
# table draws no distinction between which object each state "belongs" to),
# recomputed by crud/rental_payment.py:recompute_obligation_status -- never
# assigned directly, same discipline as models/finance.py:Obligation.status.
# CONFIRMED collapses the ZR-PAY-002-era CONFIRMED_BY_RECIPIENT/
# CONFIRMED_BY_PROVIDER pair into the one status value Section 16 names --
# "who confirmed" is still fully available, just on RentalPaymentRecord.provenance
# below, not duplicated onto status too (frontend already renders provenance
# as its own field, never relied on the status value alone for this).
# PAYMENT_SESSION_STARTED is produced by
# crud/external_payment_session.py:create_session via this same recompute
# function. PROVIDER_PROCESSING is modeled for schema completeness but has
# no producing code path yet -- this build's Stripe integration doesn't
# distinguish "provider accepted, still settling" from STARTED/SUCCEEDED/
# FAILED at the ExternalPaymentSession level (would need
# checkout.session.async_payment_succeeded handling), same honest
# "taxonomy modeled, not yet backed" posture as
# models/market_policy.py:FUNDS_FLOW_PROFILES' TRUST_ESCROW_CUSTODY/
# ZOIKO_REGULATED_CUSTODY.
RENTAL_PAYMENT_STATUSES = (
    "UPCOMING", "DUE", "PAYMENT_SESSION_STARTED", "PROVIDER_PROCESSING", "PAYER_RECORDED",
    "RECIPIENT_CONFIRMATION_PENDING", "CONFIRMED", "PARTIALLY_PAID", "OVERDUE", "DISPUTED", "REVERSED",
    "CANCELLED", "WAIVED",
)
# ZR-PAY-002 Section 6.1: the minimum provenance sources -- kept as its own,
# unrenamed vocabulary (RENTAL_PAYMENT_STATUSES above draws from Section
# 16's own separate list). PROVIDER_CONFIRMATION is backed now, by
# crud/external_payment_session.py:record_provider_payment_success.
RENTAL_PAYMENT_PROVENANCE = (
    "TENANT_DECLARATION", "RECIPIENT_CONFIRMATION", "PROVIDER_CONFIRMATION", "ADMIN_CORRECTION", "SYSTEM_DERIVATION",
)
RENTAL_PAYMENT_METHOD_CATEGORIES = ("BANK_TRANSFER", "CASH", "CARD", "OTHER")
RENTAL_PAYMENT_DISCREPANCY_REASONS = ("NOT_ARRIVED", "AMOUNT_DIFFERENT", "REFERENCE_MISMATCH", "RETURNED_OR_REVERSED", "OTHER")
RENTAL_PAYMENT_DISPUTE_STATUSES = ("OPEN", "RESOLVED")
# ZR-PAY-002 Section 9.1: same create-then-confirm shape as
# models/finance.py:PayoutBeneficiary's own PENDING_VERIFICATION -> VERIFIED
# (renamed ACTIVE here to match this domain's own vocabulary) -> SUPERSEDED
# state machine -- see crud/rental_payment.py's own module docstring for why
# this is a separate table rather than a reuse of PayoutBeneficiary.
# PENDING_REVIEW/REJECTED extend that shape for Section 9.1 step 7: 'High-risk
# changes can be routed to manual review before becoming active' -- a
# strong-auth-verified change still doesn't reach ACTIVE when it was flagged
# high-risk at submission; an admin must approve or reject it first.
RENTAL_PAYMENT_INSTRUCTION_STATUSES = ("PENDING_VERIFICATION", "PENDING_REVIEW", "ACTIVE", "SUPERSEDED", "REJECTED")
# ZR-PAY-002 Section 10/13: 'legal hold and deletion exceptions.' Same
# ACTIVE/RELEASED shape as models/dispute_legal_hold.py:DisputeLegalHold,
# scoped to EvidenceArtifact instead of DisputeEvidenceItem -- see
# RentalPaymentEvidenceHold's own docstring for why this is its own table
# rather than a reuse of that one.
RENTAL_PAYMENT_EVIDENCE_HOLD_STATUSES = ("ACTIVE", "RELEASED")


class RentalPaymentObligation(Base):
    """What a tenant owes another party (never Zoiko) for one rent/deposit
    period -- the record/evidence-layer counterpart to
    models/finance.py:Obligation, created alongside it, never in place of
    it. `status` is display-derived, never assigned directly except by
    crud/rental_payment.py:recompute_obligation_status and the explicit
    waive/cancel actions below."""

    __tablename__ = "rental_payment_obligations"

    id: Mapped[int] = mapped_column(primary_key=True)
    obligation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    agreement_id: Mapped[int | None] = mapped_column(ForeignKey("agreements.id", ondelete="CASCADE"), nullable=True, index=True)
    occupancy_id: Mapped[int | None] = mapped_column(ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=True, index=True)
    tenant_guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id", ondelete="CASCADE"), nullable=False, index=True)
    # The authorized recipient -- a landlord/agent Party, never Zoiko (A2:
    # "no rental obligation can resolve to Zoiko Rooms as the payee").
    recipient_party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    due_date: Mapped[date] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="UPCOMING")
    waived_reason: Mapped[str] = mapped_column(String(2000), default="")
    waived_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    waived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ZR-PAY-002 Section 14 'Payment due / approaching -> Tenant' -- idempotent
    # marker for services/rental_payment_due_soon.py's sweep, same
    # never-re-notify-once-sent discipline as
    # OccupancyEligibilityCheck.follow_up_notified_at.
    due_soon_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    tenant: Mapped["Guest"] = relationship()
    recipient: Mapped["Party"] = relationship()
    # One-directional only -- Agreement/Occupancy don't declare a matching
    # back_populates for this mirrored evidence-only row (see this module's
    # own docstring on why the two domains stay independent).
    agreement: Mapped["Agreement | None"] = relationship(viewonly=True)
    occupancy: Mapped["Occupancy | None"] = relationship(viewonly=True)
    records: Mapped[list["RentalPaymentRecord"]] = relationship(back_populates="obligation", order_by="RentalPaymentRecord.created_at")

    @property
    def room(self) -> "Room | None":
        """Same agreement-or-occupancy -> room traversal jurisdiction_code
        below (and crud/external_payment_session.py:create_session,
        crud/payment_connection.py's room-scoped SUSPENDED/jurisdiction
        checks) all go through -- never re-derived inline, kept as a plain
        ORM property (not a crud helper) purely so it and jurisdiction_code
        stay plain attributes pydantic's from_attributes can read directly,
        with no models->crud import cycle."""
        if self.agreement:
            return self.agreement.offer.listing.room
        if self.occupancy:
            return self.occupancy.room
        return None

    @property
    def jurisdiction_code(self) -> str | None:
        room = self.room
        return room.property.jurisdiction_code if room and room.property else None

    @property
    def display_label(self) -> str:
        """ZR-PAY-002 Section 10: the jurisdiction-resolved term to show for
        this obligation -- 'rent' universally, but DEPOSIT resolves to
        whatever this jurisdiction actually calls it (tenancy deposit / bond
        / security deposit / ...), never a hard-coded label."""
        if self.obligation_type == "DEPOSIT":
            return resolve_deposit_terminology(self.jurisdiction_code)
        return self.obligation_type.lower()


class RentalPaymentRecord(Base):
    """ZR-PAY-002 Section 4.3/6: one tenant declaration (and its subsequent
    confirmation/dispute) against an obligation -- never proof funds
    actually settled on its own (A3). `status`/`provenance` are never
    displayed collapsed into one generic 'Paid' state (Section 6.1's display
    rule) -- the API always exposes both."""

    __tablename__ = "rental_payment_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    obligation_id: Mapped[int] = mapped_column(ForeignKey("rental_payment_obligations.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="PAYER_RECORDED")
    provenance: Mapped[str] = mapped_column(String(30), default="TENANT_DECLARATION")
    declared_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    declared_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    declared_date: Mapped[date] = mapped_column(nullable=False)
    payment_method_category: Mapped[str] = mapped_column(String(20), default="OTHER")
    external_reference: Mapped[str] = mapped_column(String(255), default="")
    declared_by_guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id", ondelete="CASCADE"), nullable=False)
    confirmed_by_party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ZR-PAY-002 Section 6: 'PARTIALLY_PAID -- Confirmed amount is less than
    # obligation.' Null until a confirmation (recipient or provider) is
    # recorded; set to the full declared_amount for an ordinary full
    # confirmation, or a lesser amount for a partial one -- see
    # crud/rental_payment.py:confirm_receipt's own status derivation.
    confirmed_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    # ZR-PAY-002 Section 12.1's shared 'provider_event_reference' object,
    # applied here -- the external provider's own transaction/reconciliation
    # reference a PROVIDER_CONFIRMATION-provenance confirmation is backed
    # by. Never set for any other provenance.
    provider_reference: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    obligation: Mapped["RentalPaymentObligation"] = relationship(back_populates="records")
    declared_by: Mapped["Guest"] = relationship()
    confirmed_by: Mapped["Party | None"] = relationship()
    disputes: Mapped[list["RentalPaymentDispute"]] = relationship(back_populates="record")
    corrections: Mapped[list["RentalPaymentCorrection"]] = relationship(back_populates="record")


class RentalPaymentDispute(Base):
    """ZR-PAY-002 Section 5.2: 'A discrepancy changes the record status but
    does not trigger a Zoiko Rooms refund.' Either side may open one
    (Section 11: tenant or landlord/agent) -- exactly one of
    reported_by_guest_id/reported_by_party_id is set."""

    __tablename__ = "rental_payment_disputes"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("rental_payment_records.id", ondelete="CASCADE"), nullable=False, index=True)
    reason_code: Mapped[str] = mapped_column(String(30), nullable=False)
    details: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    reported_by_guest_id: Mapped[str | None] = mapped_column(ForeignKey("guests.id"), nullable=True)
    reported_by_party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"), nullable=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_notes: Mapped[str] = mapped_column(String(2000), default="")

    record: Mapped["RentalPaymentRecord"] = relationship(back_populates="disputes")


class RentalPaymentCorrection(Base):
    """ZR-PAY-002 Section 7.2: 'Confirmed financial records are append-only.
    A correction creates a new event linked to the original record; the
    original values remain available.' Never an UPDATE to the record it
    corrects -- this table is the only way a already-set field's history is
    ever changed, and it is itself never edited or deleted (A10).

    Section 11's permission table gives Tenant/Landlord-Agent 'Controlled'
    (not just Admin 'Restricted + reason') append rights -- exactly one of
    actor_admin_id/actor_guest_id/actor_party_id is set, recording which of
    the three actually made the change (see crud/rental_payment.py:
    tenant_correct_own_record's own narrow field/timing allow-list for what
    a non-admin actor may ever touch here)."""

    __tablename__ = "rental_payment_corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("rental_payment_records.id", ondelete="CASCADE"), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(50), nullable=False)
    previous_value: Mapped[str] = mapped_column(String(500), default="")
    new_value: Mapped[str] = mapped_column(String(500), default="")
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    actor_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    actor_guest_id: Mapped[str | None] = mapped_column(ForeignKey("guests.id"), nullable=True)
    actor_party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    record: Mapped["RentalPaymentRecord"] = relationship(back_populates="corrections")


class RentalPaymentEvidenceHold(Base):
    """ZR-PAY-002 Section 10/13: 'legal hold and deletion exceptions' --
    same ACTIVE/RELEASED, who/why/when shape as
    models/dispute_legal_hold.py:DisputeLegalHold, kept as its own table
    (rather than a reuse of it) because it holds a generic EvidenceArtifact
    row -- rental payment evidence, not a DisputeEvidenceItem -- and this
    domain stays independent of the dispute-resolution domain's own tables.
    services/evidence_retention.py:sweep_expired_evidence refuses to delete
    the underlying file for any artifact with an ACTIVE row here, the same
    real enforcement DisputeLegalHold gives crud/dispute_evidence.py:
    archive_evidence."""

    __tablename__ = "rental_payment_evidence_holds"

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("evidence_artifacts.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE")
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    placed_by_admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    released_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RentalPaymentInstruction(Base):
    """ZR-PAY-002 Section 9: the landlord/agent's own payment details for
    receiving rent/deposit directly -- Zoiko never receives, holds or
    forwards this money, and never stores enough of the account identifier
    to move any either (only `account_identifier_last4` is ever persisted,
    same AC-33-style masking discipline as
    models/finance.py:PayoutBeneficiary.account_number_last4). Step-up-authed
    via a mailed one-time code, same mechanic as PayoutBeneficiary's own
    confirm step -- kept as its own table rather than a reuse of it because
    this is a tenant-facing rent/deposit instruction a Party manages for
    itself, not Zoiko's own outgoing host-payout destination; conflating the
    two would blur exactly the domain boundary ZR-PAY-002 Section 12.1 draws.
    At most one ACTIVE row per party (partial unique index below)."""

    __tablename__ = "rental_payment_instructions"
    __table_args__ = (
        Index(
            "uq_rental_payment_instructions_active_party", "party_id", unique=True,
            postgresql_where=text("status = 'ACTIVE'"), sqlite_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING_VERIFICATION")
    method: Mapped[str] = mapped_column(String(20), default="BANK_TRANSFER")
    recipient_name: Mapped[str] = mapped_column(String(200), nullable=False)
    account_identifier_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    reference_format: Mapped[str] = mapped_column(String(255), default="")
    additional_instructions: Mapped[str] = mapped_column(String(2000), default="")
    verification_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_attempts: Mapped[int] = mapped_column(default=0)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ZR-PAY-002 Section 9.1 step 3: 'Risk controls evaluate recent credential
    # changes, device/session anomalies and high-risk account signals.'
    # Evaluated once, at submission -- see crud/rental_payment.py's own risk
    # assessment docstring for which signal this build actually checks.
    is_high_risk: Mapped[bool] = mapped_column(default=False)
    high_risk_reason: Mapped[str] = mapped_column(String(255), default="")
    reviewed_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_reason: Mapped[str] = mapped_column(String(2000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    party: Mapped["Party"] = relationship()
