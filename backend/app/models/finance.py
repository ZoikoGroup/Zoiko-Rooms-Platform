from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

OBLIGATION_TYPES = ("RENT", "DEPOSIT", "FEE", "TAX")
# Which money-plane a movement belongs to -- kept as a real column (not just a naming
# convention) so reconciliation can sum per-plane and actually detect if occupancy
# money or safeguarded deposits ever leaked into Zoiko's own revenue plane.
MONEY_PLANES = ("OCCUPANCY", "SAFEGUARDED", "REVENUE")
OBLIGATION_TYPE_TO_PLANE = {"RENT": "OCCUPANCY", "FEE": "OCCUPANCY", "TAX": "OCCUPANCY", "DEPOSIT": "SAFEGUARDED"}
OBLIGATION_STATUSES = ("PENDING", "PARTIALLY_PAID", "PAID", "WAIVED", "FAILED", "REFUNDED")

SIMULATED_PAYMENT_STATUSES = ("PENDING", "SUCCEEDED", "FAILED")
# FROZEN: an unresolved claim exists against this deposit -- release/forfeit of the
# disputed portion is blocked until the claim reaches AGREED or RESOLVED (per
# ZR-ENG-CLR-002 Section 12.2 custody state model).
DEPOSIT_STATUSES = ("HELD", "RELEASED", "FORFEITED", "PARTIALLY_RELEASED", "FROZEN")

# India-scope MVP of the instrument taxonomy in ZR-ENG-CLR-002 Section 2.3 -- only
# the two instruments this platform actually issues today. The other canonical
# instruments (RENTAL_BOND, HOLDING_DEPOSIT, KEY_ACCESS_DEPOSIT, etc.) are deferred
# until a market pack actually needs them; the field exists so they can be added
# without a schema change.
DEPOSIT_INSTRUMENT_TYPES = ("SECURITY_DEPOSIT", "RENT_DEPOSIT")
# India has no statutory custody scheme for residential deposits, so the only
# custody model this MVP implements is the host/agent holding it directly.
DEPOSIT_CUSTODY_MODELS = ("HOST_OR_AGENT",)

# Section 9.1's global deduction taxonomy, trimmed to categories with a clear
# lawful basis under Indian tenancy practice -- OTHER_CONTRACTUAL_DAMAGE is
# intentionally excluded from this MVP since it requires a market-pack-defined
# legal category, which India doesn't have configured yet.
DEPOSIT_CLAIM_CATEGORIES = (
    "UNPAID_RENT",
    "DAMAGE_BEYOND_NORMAL_WEAR",
    "MISSING_ITEMS_OR_KEYS",
    "CLEANING_REMEDIATION",
    "UTILITIES_OR_OTHER_OCCUPANCY_CHARGES",
)
# Section 12.3's claim/dispute state machine, collapsed to the states this MVP
# actually drives (no external ADR/tribunal handoff yet -- Admin resolves disputes
# directly, which Section 10.2 permits only because no market pack designates an
# external adjudicator for India in this build).
DEPOSIT_CLAIM_STATUSES = ("RENTER_RESPONSE_PENDING", "AGREED", "DISPUTED", "RESOLVED")
DEPOSIT_CLAIM_ITEM_RESPONSES = ("ACCEPT", "PARTIAL_ACCEPT", "DISPUTE")
PAYOUT_STATUSES = ("PENDING", "PAID", "FAILED", "HELD")
REFUND_STATUSES = ("REQUESTED", "APPROVED", "REJECTED", "COMPLETED")
DISPUTE_CATEGORIES = ("CHARGEBACK", "COMPENSATION", "OTHER")
DISPUTE_STATUSES = ("OPEN", "RESOLVED", "REJECTED")
RECONCILIATION_STATUSES = ("CLEAN", "DISCREPANCIES_FOUND")

# ZR-ENG-CLR-005 Section 15.1/19.2: one reason code per run_reconciliation
# check that can fail -- see crud/finance.py:run_reconciliation. NEGATIVE_
# ACCOUNT_BALANCE is raised outside reconciliation, at the moment a refund
# actually drives a host-payable/deposit-custody account negative (Section
# 21's "Negative Host balance" edge case) -- see crud/finance.py:decide_refund.
FINANCIAL_HOLD_REASON_CODES = (
    "TRIAL_BALANCE_MISMATCH", "LEDGER_ALLOCATION_MISMATCH", "AGGREGATE_MISMATCH", "NEGATIVE_ACCOUNT_BALANCE",
)
FINANCIAL_HOLD_SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
FINANCIAL_HOLD_STATUSES = ("OPEN", "RESOLVED")

# ZR-ENG-CLR-005 ledger foundation: PLATFORM_CLEARING/PLATFORM_FEE_REVENUE are
# platform-wide (party_id null); HOST_PAYABLE/DEPOSIT_CUSTODY_LIABILITY are
# per-party. Cash-basis only -- no renter-receivable account, since entries are
# posted when money actually moves (payment confirmed, payout paid, deposit
# released/forfeited, refund completed), not when an Obligation is created.
LEDGER_ACCOUNT_TYPES = ("PLATFORM_CLEARING", "HOST_PAYABLE", "DEPOSIT_CUSTODY_LIABILITY", "PLATFORM_FEE_REVENUE")

# ZR-ENG-CLR-005 Section 7.1: intentionally single-valued today -- OfferTerms
# has no cadence field and no product surface lets a host pick anything but
# implicit monthly, so WEEKLY/FORTNIGHTLY/BIWEEKLY/UPFRONT/CUSTOM stay
# undefined until something can actually select them.
PAYMENT_SCHEDULE_CADENCES = ("MONTHLY",)
# ZR-ENG-CLR-005 Section 7.2: a rent-changing agreement amendment reaching
# EFFECTIVE (crud/leasing.py:freeze_agreement_version) supersedes the
# current schedule with a new ACTIVE version -- see
# crud/leasing.py:_supersede_payment_schedule_if_rent_changed. No other
# transition (e.g. ENDED at occupancy end) exists yet.
PAYMENT_SCHEDULE_STATUSES = ("ACTIVE", "SUPERSEDED")


class Obligation(Base):
    """An explicit money-typed line item -- rent, deposit, fee or tax -- never a
    generic 'amount due' blob. `status` is only ever set by
    crud/finance.py:recompute_obligation_status(), never assigned directly, so it
    can't drift from the actual allocation sum."""

    __tablename__ = "obligations"

    id: Mapped[int] = mapped_column(primary_key=True)
    obligation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    money_plane: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    agreement_id: Mapped[int | None] = mapped_column(ForeignKey("agreements.id", ondelete="CASCADE"), nullable=True, index=True)
    occupancy_id: Mapped[int | None] = mapped_column(ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=True, index=True)
    payout_id: Mapped[int | None] = mapped_column(ForeignKey("payout_records.id"), nullable=True)
    # ZR-ENG-CLR-005 AC-02/AC-07: set only for a RENT obligation generated from
    # a PaymentSchedule (crud/leasing.py:create_agreement,
    # crud/occupancy.py:generate_next_rent_obligation) -- null for DEPOSIT and
    # any obligation created outside that path.
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("payment_schedules.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    agreement: Mapped["Agreement"] = relationship(back_populates="obligations")
    occupancy: Mapped["Occupancy"] = relationship(back_populates="obligations")
    payout: Mapped["PayoutRecord"] = relationship(back_populates="obligations")
    allocations: Mapped[list["PaymentAllocation"]] = relationship(back_populates="obligation")
    deposit_record: Mapped["DepositRecord"] = relationship(back_populates="obligation", uselist=False)


class SimulatedPayment(Base):
    """Split into create-intent (PENDING) / confirm (SUCCEEDED) steps -- mirroring a
    real processor's create-intent/webhook-confirm shape -- so a real Stripe/Razorpay
    adapter can later replace only the confirmation step. `idempotency_key` is unique
    at the DB level so a retried request cannot create a duplicate financial effect."""

    __tablename__ = "simulated_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ZR-ENG-CLR-005 AC-03/Section 4.1: the occupant (guest_id above) and the payer
    # are not always the same person -- a parent/employer/guarantor can pay on the
    # occupant's behalf. payer_guest_id is set when the payer is itself a
    # registered guest account; payer_name/email/phone describe a payer with no
    # platform account. Defaults to payer_guest_id == guest_id (payer == occupant)
    # when no payer is specified, so every existing call site is unaffected.
    payer_guest_id: Mapped[str | None] = mapped_column(ForeignKey("guests.id", ondelete="SET NULL"), nullable=True, index=True)
    payer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payer_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    guest: Mapped["Guest"] = relationship(foreign_keys=[guest_id])
    payer_guest: Mapped["Guest | None"] = relationship(foreign_keys=[payer_guest_id])
    allocations: Mapped[list["PaymentAllocation"]] = relationship(back_populates="payment", cascade="all, delete-orphan")
    receipt: Mapped["PaymentReceipt"] = relationship(back_populates="payment", uselist=False)


class PaymentReceipt(Base):
    """ZR-ENG-CLR-005 Section 13.1/13.2, AC-25: the immutable receipt for one
    successful SimulatedPayment. Rendered and hashed exactly once -- same
    render-once-then-persist discipline as leasing.py's DocumentArtifact/
    freeze_agreement_version, reusing core/receipt_documents.py's identical
    on-disk storage pattern as agreement_documents.py."""

    __tablename__ = "payment_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("simulated_payments.id", ondelete="CASCADE"), unique=True, nullable=False)
    receipt_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    payment: Mapped["SimulatedPayment"] = relationship(back_populates="receipt")


class PaymentAllocation(Base):
    """Explicit payment-to-obligation allocation. A negative `amount_allocated` row
    represents a reversing refund allocation against the same obligation, so
    reconciliation can sum payments-minus-refunds per obligation instead of tracking
    two disconnected totals."""

    __tablename__ = "payment_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("simulated_payments.id", ondelete="CASCADE"), nullable=False, index=True)
    obligation_id: Mapped[int] = mapped_column(ForeignKey("obligations.id", ondelete="CASCADE"), nullable=False, index=True)
    amount_allocated: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    payment: Mapped["SimulatedPayment"] = relationship(back_populates="allocations")
    obligation: Mapped["Obligation"] = relationship(back_populates="allocations")


class DepositRecord(Base):
    """Tracks a deposit's hold/release lifecycle independently of the underlying
    DEPOSIT-type Obligation, since deposits are held for the length of an occupancy
    and only later released or forfeited."""

    __tablename__ = "deposit_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    obligation_id: Mapped[int] = mapped_column(ForeignKey("obligations.id", ondelete="CASCADE"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="HELD")
    held_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    released_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(String(2000), default="")

    obligation: Mapped["Obligation"] = relationship(back_populates="deposit_record")
    instrument: Mapped["DepositInstrument"] = relationship(back_populates="deposit_record", uselist=False, cascade="all, delete-orphan")
    claims: Mapped[list["DepositClaim"]] = relationship(back_populates="deposit_record", cascade="all, delete-orphan")


class DepositInstrument(Base):
    """Canonical instrument classification for a deposit, kept separate from
    DepositRecord's hold/release lifecycle (ZR-ENG-CLR-002 Section 2.3, 12).
    `calculation_snapshot` is the immutable amount/formula basis Section 5.2
    requires so a later rent change can't silently alter an existing obligation."""

    __tablename__ = "deposit_instruments"

    id: Mapped[int] = mapped_column(primary_key=True)
    deposit_record_id: Mapped[int] = mapped_column(ForeignKey("deposit_records.id", ondelete="CASCADE"), unique=True, nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(30), default="SECURITY_DEPOSIT")
    custody_model: Mapped[str] = mapped_column(String(30), default="HOST_OR_AGENT")
    calculation_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    deposit_record: Mapped["DepositRecord"] = relationship(back_populates="instrument")


class DepositClaim(Base):
    """A Host-submitted itemized deduction claim against a held deposit
    (ZR-ENG-CLR-002 Section 9-10). The Host is the claimant, never the automatic
    adjudicator -- the renter must accept or dispute each line item before any
    disputed amount can be released or forfeited."""

    __tablename__ = "deposit_claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    deposit_record_id: Mapped[int] = mapped_column(ForeignKey("deposit_records.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="RENTER_RESPONSE_PENDING")
    submitted_by_admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    renter_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_notes: Mapped[str] = mapped_column(String(2000), default="")

    deposit_record: Mapped["DepositRecord"] = relationship(back_populates="claims")
    items: Mapped[list["DepositClaimItem"]] = relationship(back_populates="claim", cascade="all, delete-orphan")


class DepositClaimItem(Base):
    """One deduction line item on a claim (ZR-ENG-CLR-002 Section 9.3). Evidence
    fields mirror IdentityVerification's upload pattern: only the generated
    `evidence_filename` is ever persisted or used to build a path, never anything
    client-supplied."""

    __tablename__ = "deposit_claim_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("deposit_claims.id", ondelete="CASCADE"), nullable=False, index=True)
    category_code: Mapped[str] = mapped_column(String(50), nullable=False)
    amount_requested: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), default="")
    evidence_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence_original_name: Mapped[str] = mapped_column(String(255), default="")
    evidence_content_type: Mapped[str] = mapped_column(String(100), default="")
    tenant_response: Mapped[str] = mapped_column(String(20), default="")
    final_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    claim: Mapped["DepositClaim"] = relationship(back_populates="items")


class PayoutRecord(Base):
    """`period_key` (e.g. "2026-08") is unique with `party_id` as a double-click
    guard on the "run payout" action, since a simulated payout has no real processor
    idempotency key to lean on. `run_payout()` row-locks eligible obligations
    (`with_for_update()`) so two concurrent runs can't both grab the same rows."""

    __tablename__ = "payout_records"
    __table_args__ = (UniqueConstraint("party_id", "period_key", name="uq_payout_party_period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False)
    period_key: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    hold_reason: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    party: Mapped["Party"] = relationship()
    obligations: Mapped[list["Obligation"]] = relationship(back_populates="payout")
    statement: Mapped["PayoutStatement"] = relationship(back_populates="payout", uselist=False)
    service_fee_invoice: Mapped["ServiceFeeInvoice"] = relationship(back_populates="payout", uselist=False)


class PayoutStatement(Base):
    """ZR-ENG-CLR-005 Section 6.3/13.1: the immutable statement for one PAID
    PayoutRecord -- gross rent, Zoiko fee and net payout, same render-once-
    then-persist discipline as PaymentReceipt, reusing
    core/payout_statement_documents.py's identical on-disk storage pattern.
    Never generated for a HELD payout -- nothing was actually paid out yet."""

    __tablename__ = "payout_statements"

    id: Mapped[int] = mapped_column(primary_key=True)
    payout_id: Mapped[int] = mapped_column(ForeignKey("payout_records.id", ondelete="CASCADE"), unique=True, nullable=False)
    statement_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    payout: Mapped["PayoutRecord"] = relationship(back_populates="statement")


class ServiceFeeInvoice(Base):
    """ZR-ENG-CLR-005 Section 13.1/AC-26: the immutable Zoiko service-fee
    invoice for one PAID PayoutRecord's fee line. legal_entity_name/tax_rate
    are frozen copies of the resolved MarketPolicyPack fields at issuance
    time (AC-27-style discipline -- never re-read from a possibly-since-
    changed policy row), same render-once-then-persist pattern as
    PayoutStatement/PaymentReceipt."""

    __tablename__ = "service_fee_invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    payout_id: Mapped[int] = mapped_column(ForeignKey("payout_records.id", ondelete="CASCADE"), unique=True, nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    legal_entity_name: Mapped[str] = mapped_column(String(200), nullable=False)
    tax_registration_number: Mapped[str] = mapped_column(String(50), default="")
    fee_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    tax_rate: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    tax_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    payout: Mapped["PayoutRecord"] = relationship(back_populates="service_fee_invoice")


class RefundRequest(Base):
    """AC-14: idempotency_key is unique at the DB level, same as
    SimulatedPayment's -- a retried request_refund call cannot create a
    second RefundRequest row for the same refund."""

    __tablename__ = "refund_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("simulated_payments.id", ondelete="CASCADE"), nullable=False)
    obligation_id: Mapped[int] = mapped_column(ForeignKey("obligations.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), default="")
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="REQUESTED")
    requested_by_admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    decided_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    payment: Mapped["SimulatedPayment"] = relationship()
    obligation: Mapped["Obligation"] = relationship()


class DisputeCase(Base):
    """ZR-ENG-CLR-005 Section 20/AC-32: obligation_id/amount/chargeback_outcome
    are only populated for category=CHARGEBACK -- category is never validated
    against DISPUTE_CATEGORIES for any other value (COMPENSATION/OTHER stay
    exactly as generic as before), so those three columns stay null for a
    non-chargeback dispute. chargeback_outcome ("WON"/"LOST") is deliberately
    separate from `status` (RESOLVED/REJECTED): status has no established
    money-movement meaning and is shared with non-financial dispute
    categories -- see crud/finance.py:resolve_dispute."""

    __tablename__ = "dispute_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("simulated_payments.id", ondelete="CASCADE"), nullable=True)
    occupancy_id: Mapped[int | None] = mapped_column(ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=True)
    obligation_id: Mapped[int | None] = mapped_column(ForeignKey("obligations.id", ondelete="CASCADE"), nullable=True)
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    chargeback_outcome: Mapped[str | None] = mapped_column(String(10), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_notes: Mapped[str] = mapped_column(String(2000), default="")

    payment: Mapped["SimulatedPayment"] = relationship()
    occupancy: Mapped["Occupancy"] = relationship()
    obligation: Mapped["Obligation"] = relationship()


class FinancialHold(Base):
    """ZR-ENG-CLR-005 Section 15.1/19.2: a queryable, resolvable finance
    exception -- today, the only producer is crud/finance.py:run_reconciliation,
    one row per failed check, replacing what used to be only a plain string in
    ReconciliationRun.mismatches. `source_type`/`source_id` mirror LedgerEntry's
    traceability pair. Scope note: this is the reconciliation-exception queue,
    not a general per-obligation/per-payout hold mechanism -- PayoutRecord's own
    `hold_reason` is untouched and unrelated. No SLA/owner/evidence fields exist
    here, matching DisputeCase (the closest existing precedent), which has none
    either."""

    __tablename__ = "financial_holds"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_id: Mapped[str] = mapped_column(String(50), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    description: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    resolution_notes: Mapped[str] = mapped_column(String(2000), default="")


class PaymentSchedule(Base):
    """ZR-ENG-CLR-005 Section 7.1/15.1: the versioned plan a RENT obligation
    series is generated from -- created once, alongside the agreement's own
    AgreementVersion snapshot, so a rent obligation is demonstrably traceable
    to a specific schedule (AC-02) rather than an ad-hoc same-amount-every-
    time assumption. `amount` is a frozen copy of the rent at creation time,
    never re-read from a possibly-changed listing price (AC-07).

    Section 7.2: a rent-changing agreement amendment reaching EFFECTIVE
    supersedes the current schedule with a new row (`version` incremented,
    old row flipped to SUPERSEDED) rather than editing amount/first_due in
    place -- see crud/leasing.py:_supersede_payment_schedule_if_rent_changed.
    An agreement can therefore have more than one row over its lifetime, but
    the partial unique index below guarantees at most one ACTIVE row at a
    time; crud/occupancy.py:generate_next_rent_obligation always reads the
    ACTIVE one. Already-generated Obligation rows are never rewritten by a
    supersession -- only future generation reads the new row."""

    __tablename__ = "payment_schedules"
    __table_args__ = (
        Index(
            "uq_payment_schedules_active_agreement", "agreement_id", unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(default=1)
    cadence: Mapped[str] = mapped_column(String(20), default="MONTHLY")
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    first_due: Mapped[date] = mapped_column(Date, nullable=False)
    # Descriptive/traceability only in this build -- not read by any due-date
    # computation, which stays exactly crud/occupancy.py:_add_months as today.
    anchor_day: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    totals: Mapped[dict] = mapped_column(JSON, default=dict)
    mismatches: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="CLEAN")


class LedgerAccount(Base):
    """One row per (account_type, party, currency). party_id is null for the two
    platform-wide account types (PLATFORM_CLEARING, PLATFORM_FEE_REVENUE) and set
    for the two per-party types (HOST_PAYABLE, DEPOSIT_CUSTODY_LIABILITY). The
    unique constraint is a real backstop for party-scoped rows, but Postgres
    treats NULL as distinct so it does not by itself stop duplicate platform-wide
    rows -- services/ledger.py's get-or-create (query-then-insert) is what
    actually prevents that."""

    __tablename__ = "ledger_accounts"
    __table_args__ = (UniqueConstraint("account_type", "party_id", "currency", name="uq_ledger_account_type_party_currency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_type: Mapped[str] = mapped_column(String(30), nullable=False)
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class LedgerEntry(Base):
    """An immutable, balanced double-entry journal row -- amount is always
    positive, direction is encoded by which side (debit/credit) an account is
    on. Never edited after creation; a correction is a new offsetting entry, per
    ZR-ENG-CLR-005's ledger doctrine. `source_type`/`source_id` trace an entry
    back to the obligation/payment/payout/refund/deposit row that produced it,
    mirroring Notification's related_entity_type/related_entity_id pattern."""

    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    debit_account_id: Mapped[int] = mapped_column(ForeignKey("ledger_accounts.id"), nullable=False)
    credit_account_id: Mapped[int] = mapped_column(ForeignKey("ledger_accounts.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    description: Mapped[str] = mapped_column(String(500), default="")
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_id: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    debit_account: Mapped["LedgerAccount"] = relationship(foreign_keys=[debit_account_id])
    credit_account: Mapped["LedgerAccount"] = relationship(foreign_keys=[credit_account_id])
