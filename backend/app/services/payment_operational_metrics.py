"""ZR-WIR-TRACE-001 TR-11 / Section 3's 'Evidence' cross-cutting rule
requires observability (metrics, structured logs, tracing/correlation IDs,
error taxonomy, alert ownership) for every domain. Section1MetricsRead
(app/services/operational_metrics.py) and DisputeOperationalMetricsRead
(app/services/dispute_operational_metrics.py) already do this for their own
domains; the Payments domain (ZR-PAY-002 -- Listing Fee + rental payment
records) never had an equivalent.

Same established pattern: no metrics/observability infrastructure
(Prometheus, StatsD, etc.) exists anywhere in this codebase, so every
figure here is computed on demand from existing tables via one read-only
admin endpoint, not pushed to an external system. Rate/average helpers
return None (not 0) when there's no data to compute from -- an empty
Payments domain should read as 'nothing to report yet', not a fabricated
zero.

Deliberately NOT reported, same 'state the gap, don't approximate it'
discipline the other two files already use:

- Correlation-ID-keyed request tracing/error taxonomy -- that lives in the
  audit_events/domain_events tables themselves (now populated per
  crud/listing_fee.py and crud/rental_payment.py's own correlation_id
  threading), queried directly by a support engineer, not aggregated into
  a metric here.
- Alert ownership -- an operational/paging-config concern, not a number
  this module can compute from application tables.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.listing_fee import ListingFeePayment, ListingFeeRefund
from app.models.rental_payment import (
    RentalPaymentDispute,
    RentalPaymentInstruction,
    RentalPaymentObligation,
    RentalPaymentRecord,
)
from app.schemas.analytics import PaymentOperationalMetricsRead


def _group_counts(db: Session, column) -> dict[str, int]:
    rows = db.execute(select(column, func.count()).group_by(column)).all()
    return {key: count for key, count in rows if key is not None}


def _listing_fee_payment_failure_rate(db: Session) -> float | None:
    """Of payments that reached a terminal state (SUCCEEDED or FAILED --
    PENDING is still in flight and would understate the rate), what
    fraction failed."""
    terminal = db.scalar(
        select(func.count()).select_from(ListingFeePayment).where(ListingFeePayment.status.in_(("SUCCEEDED", "FAILED")))
    ) or 0
    if terminal == 0:
        return None
    failed = db.scalar(
        select(func.count()).select_from(ListingFeePayment).where(ListingFeePayment.status == "FAILED")
    ) or 0
    return round(failed / terminal, 4)


def _avg_listing_fee_time_to_success_seconds(db: Session) -> float | None:
    # Deltas computed in Python, not DB arithmetic -- same reasoning as
    # operational_metrics.py's own delta helpers: SQLite (the test
    # harness) and Postgres don't handle DateTime subtraction identically.
    rows = db.execute(
        select(ListingFeePayment.created_at, ListingFeePayment.paid_at).where(ListingFeePayment.paid_at.is_not(None))
    ).all()
    if not rows:
        return None
    deltas = [(paid_at - created_at).total_seconds() for created_at, paid_at in rows]
    return round(sum(deltas) / len(deltas), 2)


def _listing_fee_refund_rate(db: Session) -> float | None:
    """Of succeeded payments, what fraction have ever had a refund
    requested (any status) -- a volume signal, not a dollar-amount one."""
    succeeded = db.scalar(
        select(func.count()).select_from(ListingFeePayment).where(ListingFeePayment.status == "SUCCEEDED")
    ) or 0
    if succeeded == 0:
        return None
    refunded_payment_count = db.scalar(
        select(func.count(func.distinct(ListingFeeRefund.payment_id)))
    ) or 0
    return round(refunded_payment_count / succeeded, 4)


def _avg_rental_payment_confirmation_turnaround_days(db: Session) -> float | None:
    """Time from the tenant's own record (its created_at -- the
    mark-paid moment) to the recipient/provider/admin's confirmed_at, for
    every record that ever reached a confirmed_at. A discrepancy-then-
    confirmed record still counts -- confirmed_at is set exactly once, at
    the moment of actual confirmation, regardless of any dispute detour."""
    rows = db.execute(
        select(RentalPaymentRecord.created_at, RentalPaymentRecord.confirmed_at).where(
            RentalPaymentRecord.confirmed_at.is_not(None)
        )
    ).all()
    if not rows:
        return None
    deltas = [(confirmed_at - created_at).total_seconds() for created_at, confirmed_at in rows]
    return round(sum(deltas) / len(deltas) / 86400, 2)


def _rental_payment_discrepancy_rate(db: Session, total_records: int) -> float | None:
    if total_records == 0:
        return None
    disputed_record_count = db.scalar(
        select(func.count(func.distinct(RentalPaymentDispute.record_id)))
    ) or 0
    return round(disputed_record_count / total_records, 4)


def compute_payment_operational_metrics(db: Session) -> PaymentOperationalMetricsRead:
    total_listing_fee_payments = db.scalar(select(func.count(ListingFeePayment.id))) or 0
    total_listing_fee_refunds = db.scalar(select(func.count(ListingFeeRefund.id))) or 0
    total_obligations = db.scalar(select(func.count(RentalPaymentObligation.id))) or 0
    total_records = db.scalar(select(func.count(RentalPaymentRecord.id))) or 0

    return PaymentOperationalMetricsRead(
        total_listing_fee_payments=total_listing_fee_payments,
        listing_fee_payments_by_status=_group_counts(db, ListingFeePayment.status),
        listing_fee_payment_failure_rate=_listing_fee_payment_failure_rate(db),
        avg_listing_fee_time_to_success_seconds=_avg_listing_fee_time_to_success_seconds(db),
        total_listing_fee_refunds=total_listing_fee_refunds,
        listing_fee_refunds_by_status=_group_counts(db, ListingFeeRefund.status),
        listing_fee_refund_rate=_listing_fee_refund_rate(db),
        total_rental_payment_obligations=total_obligations,
        obligations_by_status=_group_counts(db, RentalPaymentObligation.status),
        total_rental_payment_records=total_records,
        records_by_status=_group_counts(db, RentalPaymentRecord.status),
        avg_rental_payment_confirmation_turnaround_days=_avg_rental_payment_confirmation_turnaround_days(db),
        rental_payment_discrepancy_rate=_rental_payment_discrepancy_rate(db, total_records),
        payment_instructions_pending_review_count=(
            db.scalar(
                select(func.count()).select_from(RentalPaymentInstruction).where(
                    RentalPaymentInstruction.status == "PENDING_REVIEW"
                )
            ) or 0
        ),
        payment_instructions_rejected_count=(
            db.scalar(
                select(func.count()).select_from(RentalPaymentInstruction).where(
                    RentalPaymentInstruction.status == "REJECTED"
                )
            ) or 0
        ),
    )
