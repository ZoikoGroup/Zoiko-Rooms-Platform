"""ZR-ENG-CLR-004 Section 8.3/9.1/AC-13: Executed and Effective are separate
states. 'An agreement can be fully signed today but commence on a later
date... The platform must never equate last signature received with
occupancy is active.'

This codebase's Agreement.status already reaches its terminal 'SIGNED' value
only once both parties have signed AND the initial rent/deposit obligations
have cleared (see crud/leasing.py:confirm_agreement_payment) -- that's
EXECUTED in this spec's terms. EFFECTIVE additionally requires the lease's
own commencement date (OfferTerms.start_date, already captured at offer-terms
time) to have arrived. No new effectiveness-rule config layer is invented
here -- this reuses a date that already exists in this codebase's data.
"""

from __future__ import annotations

from datetime import date

from app.models.leasing import Agreement


def is_agreement_effective(agreement: Agreement, *, today: date | None = None) -> bool:
    """True only once the agreement is EXECUTED (status == 'SIGNED') and the
    lease's own start_date has arrived. False for a fully-signed agreement
    whose term hasn't started yet -- see crud/eligibility.py:
    check_move_in_eligibility, which gates occupancy activation on this,
    not merely on agreement.status."""
    if agreement.status != "SIGNED":
        return False
    offer = agreement.offer
    if not offer or not offer.terms:
        return False
    today = today or date.today()
    return today >= offer.terms[-1].start_date
