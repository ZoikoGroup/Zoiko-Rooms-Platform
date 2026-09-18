"""ZR-ENG-CLR-001 Section 1, Rule 6 / Section 9: occupant-level overlap risk
evaluation. Account holder/payer (the Guest who applied) and Named Occupant
are distinct roles -- overlap must be evaluated against whichever Guest will
actually occupy the room (Application.occupant_guest_id), never merely the
applying/paying account, so one person can't dodge scrutiny by having someone
else apply on their behalf, and one payer booking for several different real
occupants is never flagged at all.

Deliberately produces a risk TIER, never a hard block by itself (9.1: "Risk
controls should flag suspicious overlap patterns rather than relying on a
universal one-booking-per-account prohibition") -- crud/leasing.py decides
what a BLOCK tier actually does (require an admin override reason), never
this module.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.leasing import Application, Offer

# 9.2: "Same occupant; short relocation overlap -- Potentially legitimate --
# Allow within configured tolerance." A single constant today (same spirit as
# CURRENT_POLICY_VERSION) rather than hard-coded deep inside the comparison.
RELOCATION_TOLERANCE_DAYS = 14

# Live Offer/Agreement states that represent an actual room-committing claim
# on an interval -- anything else (DECLINED/EXPIRED/WITHDRAWN offers, VOID
# agreements) is dead and can never conflict.
_LIVE_OFFER_STATUSES = ("ACCEPTED",)
_LIVE_AGREEMENT_STATUSES = ("SENT", "PAYMENT_IN_PROGRESS", "PAYMENT_PENDING", "SIGNED")


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return (date(year + 1, 1, 1) - date(year, 12, 1)).days
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def _interval_overlap_days(a_start: date, a_end: date, b_start: date, b_end: date) -> int:
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    return max(0, (earliest_end - latest_start).days)


def evaluate_occupant_overlap(
    db: Session,
    *,
    occupant_guest_id: str,
    listing_id: str,
    start_date: date,
    term_months: int,
    exclude_offer_id: int | None = None,
) -> tuple[str, str]:
    """Compares [start_date, start_date + term_months) against every other
    live Offer's interval already committed to this same occupant on a
    DIFFERENT listing. Returns (tier, reason) -- reason is a short,
    human-readable audit note, blank only when tier == 'NONE'."""
    requested_end = _add_months(start_date, term_months)

    candidate_offers = db.scalars(
        select(Offer)
        .join(Application, Application.id == Offer.application_id)
        .where(
            Offer.listing_id != listing_id,
            (Application.named_occupant_guest_id == occupant_guest_id)
            | (
                (Application.named_occupant_guest_id.is_(None))
                & (Application.guest_id == occupant_guest_id)
            ),
        )
    ).all()

    worst_tier, worst_reason = "NONE", ""
    for offer in candidate_offers:
        if exclude_offer_id is not None and offer.id == exclude_offer_id:
            continue
        agreement_status = offer.agreement.status if offer.agreement else None
        is_live = offer.status in _LIVE_OFFER_STATUSES or agreement_status in _LIVE_AGREEMENT_STATUSES
        if not is_live or not offer.terms:
            continue

        terms = offer.terms[-1]
        other_end = _add_months(terms.start_date, terms.term_months)
        overlap_days = _interval_overlap_days(start_date, requested_end, terms.start_date, other_end)
        if overlap_days <= 0:
            continue

        other_span_days = (other_end - terms.start_date).days
        near_full_overlap = overlap_days >= max(other_span_days - RELOCATION_TOLERANCE_DAYS, 0)
        tier = "BLOCK" if near_full_overlap else "REVIEW"
        reason = (
            f"Overlaps {overlap_days}d with offer #{offer.id} on listing {offer.listing_id} "
            f"({'near-full' if tier == 'BLOCK' else 'partial'} overlap)"
        )
        if tier == "BLOCK":
            return tier, reason
        if worst_tier == "NONE":
            worst_tier, worst_reason = tier, reason

    return worst_tier, worst_reason
