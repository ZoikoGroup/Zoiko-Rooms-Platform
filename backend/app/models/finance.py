from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint
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

PLATFORM_FEE_RATE = 0.10


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

    guest: Mapped["Guest"] = relationship()
    allocations: Mapped[list["PaymentAllocation"]] = relationship(back_populates="payment", cascade="all, delete-orphan")


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


class RefundRequest(Base):
    __tablename__ = "refund_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("simulated_payments.id", ondelete="CASCADE"), nullable=False)
    obligation_id: Mapped[int] = mapped_column(ForeignKey("obligations.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(20), default="REQUESTED")
    requested_by_admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    decided_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    payment: Mapped["SimulatedPayment"] = relationship()
    obligation: Mapped["Obligation"] = relationship()


class DisputeCase(Base):
    __tablename__ = "dispute_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("simulated_payments.id", ondelete="CASCADE"), nullable=True)
    occupancy_id: Mapped[int | None] = mapped_column(ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_notes: Mapped[str] = mapped_column(String(2000), default="")

    payment: Mapped["SimulatedPayment"] = relationship()
    occupancy: Mapped["Occupancy"] = relationship()


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    totals: Mapped[dict] = mapped_column(JSON, default=dict)
    mismatches: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="CLEAN")
