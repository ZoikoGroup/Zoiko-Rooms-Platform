"""Admin payments overview, in the two halves the Zoiko Rooms Payment Model
keeps apart:
- Zoiko revenue: the Listing Fee hosts pay Zoiko through Stripe -- the only
  money Zoiko actually receives.
- Rent between hosts and renters: records only. Rent is paid to the host
  directly; these figures are what's been recorded, never money Zoiko holds.

Totals are per currency -- amounts in different currencies are never added
together."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.listing_fee import ListingFeePayment, ListingFeeRefund
from app.models.rental_payment import RentalPaymentObligation
from app.models.user_account import UserAccount

# How rent obligations are grouped on the overview.
RENT_BUCKETS = {
    "confirmed": ("CONFIRMED",),
    "awaiting_host": ("RECIPIENT_CONFIRMATION_PENDING", "PAYER_RECORDED"),
    "overdue": ("OVERDUE",),
    "disputed": ("DISPUTED",),
    "due": ("UPCOMING", "DUE", "PARTIALLY_PAID", "REVERSED"),
}
RENT_LIST_LIMIT = 200


def _totals(pairs) -> list[dict]:
    sums: dict[str, float] = defaultdict(float)
    for currency, amount in pairs:
        sums[currency] += float(amount or 0)
    return [{"currency": c, "amount": round(a, 2)} for c, a in sorted(sums.items())]


def listing_fee_revenue(db: Session) -> dict:
    payments = list(db.scalars(select(ListingFeePayment)))
    refunds = list(db.scalars(
        select(ListingFeeRefund).where(ListingFeeRefund.status.in_(("REFUNDED", "PARTIALLY_REFUNDED")))
    ))
    succeeded = [p for p in payments if p.status == "SUCCEEDED"]
    return {
        "collected": _totals((p.currency, p.amount) for p in succeeded),
        "refunded": _totals((r.currency, r.amount) for r in refunds),
        "paid_count": len(succeeded),
        "pending_count": sum(1 for p in payments if p.status == "PENDING"),
        "failed_count": sum(1 for p in payments if p.status == "FAILED"),
    }


def _bucket_for(status: str) -> str | None:
    for bucket, statuses in RENT_BUCKETS.items():
        if status in statuses:
            return bucket
    return None


def _listing_name(obligation: RentalPaymentObligation) -> str:
    if obligation.agreement and obligation.agreement.offer and obligation.agreement.offer.listing:
        return obligation.agreement.offer.listing.name
    if obligation.occupancy and obligation.occupancy.listing:
        return obligation.occupancy.listing.name
    return ""


def rent_records(db: Session, *, bucket: str | None = None, search: str = "") -> dict:
    """Summary of every rent/deposit obligation by bucket, plus a filtered
    list (newest due date first) for the table."""
    obligations = list(db.scalars(
        select(RentalPaymentObligation).order_by(RentalPaymentObligation.due_date.desc(), RentalPaymentObligation.id.desc())
    ))
    host_names = {
        u.party_id: (u.full_name or u.email)
        for u in db.scalars(select(UserAccount).where(UserAccount.party_id.is_not(None)))
    }

    summary: dict[str, dict] = {}
    for name, statuses in RENT_BUCKETS.items():
        members = [o for o in obligations if o.status in statuses]
        # "Confirmed" is what the host received; everything else is what's still owed.
        pairs = (
            (o.currency, float(o.amount) - o.outstanding_amount) for o in members
        ) if name == "confirmed" else ((o.currency, o.outstanding_amount) for o in members)
        summary[name] = {"count": len(members), "totals": _totals(pairs)}

    needle = search.strip().lower()
    rows = []
    for o in obligations:
        row_bucket = _bucket_for(o.status)
        if bucket and row_bucket != bucket:
            continue
        renter = o.tenant.name if o.tenant else o.tenant_guest_id
        host = host_names.get(o.recipient_party_id, f"Party #{o.recipient_party_id}")
        listing = _listing_name(o)
        if needle and needle not in " ".join((renter, host, listing, str(o.id))).lower():
            continue
        rows.append({
            "obligation_id": o.id, "obligation_label": o.display_label, "status": o.status, "bucket": row_bucket or "",
            "amount": float(o.amount), "outstanding_amount": o.outstanding_amount, "currency": o.currency,
            "due_date": o.due_date, "renter_name": renter, "host_name": host, "listing_name": listing,
        })
        if len(rows) >= RENT_LIST_LIMIT:
            break
    return {"summary": summary, "rows": rows}
