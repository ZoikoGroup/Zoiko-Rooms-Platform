"""ZR-ENG-CLR-006 Section 12: the Refund Entitlement Calculation Engine.
'Refunds must be itemized and reproducible. The engine calculates
entitlement independently from whether the refund is presently funded or
already settled' -- this increment computes and persists the entitlement
only; execution (creating/approving an actual RefundRequest against Section
5's ledger) is a deliberately separate, later step, matching the doctrine
'Refund entitlement is not refund execution.'

Scope actually implemented, honestly: this build has no renter-facing fee or
tax concept (see crud/finance.py:get_payment_preview's identical scope note
for AC-11), so refundable_renter_fees/refundable_taxes/approved_credits are
always zero here, never fabricated. What the engine does compute for real:
which paid RENT obligations fall after the case's effective_termination_date
(unearned -> refundable) versus on/before it (earned -> non-refundable) --
no proration of a partial period is attempted, since this codebase has no
approved amount_rule/proration formula anywhere (Section 7.2 requires one to
be 'versioned' before use) -- only whole obligations are ever classified.
NOTICE_LIABILITY and MITIGATION_CREDIT ARE real, nonzero-capable computed
values (AC-12/AC-13/AC-14) -- see crud/refund_entitlement.py:
_compute_policy_liability/_compute_mitigation_credit -- driven by the
case's own frozen policy_snapshot liability model plus any MitigationRecord
evidence, not fabricated formulas invented at read time."""

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-ENG-CLR-006 Section 12.2's output line items. EARNED_RENT/
# REFUNDABLE_UNEARNED_RENT/NOTICE_LIABILITY/MITIGATION_CREDIT are all real,
# nonzero-capable computed values (see module docstring). RENTER_FEE/TAX/
# OTHER_CREDIT remain modeled for schema completeness but only ever appear
# with amount 0.00 -- no fee, tax or service-recovery-credit concept exists
# in this codebase to compute a real nonzero value from.
REFUND_LINE_ITEM_TYPES = (
    "EARNED_RENT",
    "REFUNDABLE_UNEARNED_RENT",
    "NOTICE_LIABILITY",
    "MITIGATION_CREDIT",
    "RENTER_FEE",
    "TAX",
    "OTHER_CREDIT",
)
# ZR-ENG-CLR-006 Section 16.1's own state model, trimmed to what this
# simulated build can actually distinguish -- crud/finance.py:decide_refund's
# own docstring already establishes 'Approving is completing -- there's no
# separate money-movement step in a simulated system' for Section 5's own
# RefundRequest, so the FUNDING_READY/SUBMITTED_TO_PSP/PROCESSING distinctions
# have no real state to occupy here. APPROVED is real, though: Section 16.2
# is explicit that "HYBRID. Deterministic, low-risk refunds may auto-approve.
# Manual review is required for disputes, exceptional compensation,
# insufficient Host funds, high-value thresholds, evidence-dependent
# statutory grounds, suspected abuse/fraud, legal holds or policy ambiguity" --
# this build has none of that risk-scoring infrastructure yet, so it fails
# closed the same way AC-35 already does elsewhere: every entitlement
# requires an explicit approve_refund_entitlement call (crud/
# refund_entitlement.py) before it can be executed, rather than silently
# auto-approving everything. EXECUTED is reached the moment every
# REFUNDABLE_UNEARNED_RENT line's RefundRequest is approved.
REFUND_ENTITLEMENT_STATUSES = ("CALCULATED", "APPROVED", "EXECUTED")


class RefundEntitlement(Base):
    """One versioned calculation for a termination_case -- AC-24: 'Recalculating
    after a new fact creates a new entitlement version rather than overwriting
    the prior calculation.' Never updated in place; calculate_refund_entitlement
    always inserts a new row with version = previous max + 1."""

    __tablename__ = "refund_entitlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    termination_case_id: Mapped[int] = mapped_column(ForeignKey("termination_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(default=1)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    gross_refundable: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    net_refund: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="CALCULATED")
    calculated_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    approved_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    termination_case: Mapped["TerminationCase"] = relationship()
    line_items: Mapped[list["RefundEntitlementLineItem"]] = relationship(
        back_populates="entitlement", cascade="all, delete-orphan", order_by="RefundEntitlementLineItem.id",
    )


class RefundEntitlementLineItem(Base):
    """One line of a RefundEntitlement's itemized breakdown (Section 12.2's
    'Calculation output' table). source_obligation_id is set for the two
    real line types (EARNED_RENT/REFUNDABLE_UNEARNED_RENT); null for the
    always-zero placeholder line types."""

    __tablename__ = "refund_entitlement_line_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    entitlement_id: Mapped[int] = mapped_column(ForeignKey("refund_entitlements.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_obligation_id: Mapped[int | None] = mapped_column(ForeignKey("obligations.id"), nullable=True)
    period_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    basis_note: Mapped[str] = mapped_column(String(500), default="")
    # ZR-ENG-CLR-006 Section 15/19 (refund_execution): the actual Section 5
    # RefundRequest this line's execution created (or reused, if a prior
    # entitlement version already refunded this same obligation) -- set only
    # for an executed REFUNDABLE_UNEARNED_RENT line.
    refund_request_id: Mapped[int | None] = mapped_column(ForeignKey("refund_requests.id"), nullable=True)

    entitlement: Mapped["RefundEntitlement"] = relationship(back_populates="line_items")
