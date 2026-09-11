"""ZR-ENG-CLR-005 AC-16: 'Payment succeeded does not directly change booking
state; Booking Orchestrator consumes normalized events.' Payment Service
(crud/finance.py::confirm_payment) calls into this module instead of
importing crud.leasing/crud.occupancy directly to react to a payment
clearing -- that's the actual module boundary AC-16 cares about.

Still synchronous/in-process: this stack has no queue or scheduler anywhere
(see crud/events.py:emit_event's own "No async consumer is wired up yet"
note), so a fully async orchestrator would be invented infrastructure this
foundation increment has no business adding. The obligation.satisfied event
emitted below (Section 16.2's named event) is what a later real async
consumer would subscribe to, without Payment Service changing again.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud.events import emit_event
from app.crud.leasing import confirm_agreement_payment
from app.crud.occupancy import generate_next_rent_obligation
from app.models.admin_user import AdminUser
from app.models.finance import Obligation


def confirm_downstream_agreements(db: Session, obligations: list[Obligation], *, correlation_id: str = "") -> None:
    """Called once every allocation in a confirm_payment call has been
    recorded, before the payment itself is marked SUCCEEDED/committed. An
    agreement whose initial obligations just all cleared reaches SIGNED here
    (confirm_agreement_payment is itself a no-op otherwise) -- exactly the
    same call this was extracted from crud/finance.py::confirm_payment,
    unchanged in timing or effect."""
    touched_agreements = {o.agreement for o in obligations if o.agreement_id and o.agreement}
    for agreement in touched_agreements:
        confirm_agreement_payment(db, agreement, correlation_id=correlation_id)

    for obligation in obligations:
        emit_event(
            db, "obligation.satisfied", "obligation", str(obligation.id), {},
            correlation_id=correlation_id, idempotency_key=f"obligation.satisfied:{obligation.id}",
        )


def generate_downstream_rent(db: Session, obligations: list[Obligation], admin: AdminUser) -> None:
    """Called after the payment's own commit -- best-effort, exactly as
    before extraction: an ownership mismatch here (e.g. a super_admin's own
    payment action touching another provider's occupancy) must never undo an
    already-committed successful payment, so it never propagates a failure
    back to the caller."""
    for obligation in obligations:
        if obligation.obligation_type == "RENT" and obligation.status == "PAID" and obligation.occupancy_id:
            db.refresh(obligation)
            try:
                generate_next_rent_obligation(db, obligation.occupancy, admin)
            except HTTPException:
                pass
