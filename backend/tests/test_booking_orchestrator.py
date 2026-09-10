"""ZR-ENG-CLR-005 AC-16: confirms crud/finance.py::confirm_payment reacts to a
cleared payment only through services/booking_orchestrator.py (no direct
crud.leasing/crud.occupancy imports left in crud/finance.py -- see the module
docstring there), and that the new obligation.satisfied domain event actually
gets emitted. The pre-existing behavior this was extracted from (agreement
reaching SIGNED once fully paid, recurring rent generation) is already covered
unmodified by test_finance_notifications.py and test_ledger.py."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain_event import DomainEvent
from tests.conftest import auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


class TestObligationSatisfiedEventEmitted:
    def test_confirm_payment_emits_obligation_satisfied_event(self, client, db_session: Session):
        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="orchestrator1")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "orchestrator-test-1"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        payment_id = r.json()["id"]

        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "obligation.satisfied",
                DomainEvent.resource_type == "obligation",
                DomainEvent.resource_id == str(obligation.id),
            )
        )
        assert event is not None
