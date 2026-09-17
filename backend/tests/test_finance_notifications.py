"""Confirms payment confirmation now notifies the paying renter -- this was
the one real notification gap found while auditing the lifecycle events named
in the Anil task list (identity verification, application decisions, listing
approval, payment confirmation, sublet decisions); the other four already
notified correctly at the CRUD layer."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import Obligation
from app.models.guest import Guest
from app.models.notification import Notification
from tests.conftest import _make_admin, _make_user, auth_admin_cookie


def _make_guest_linked_to_user(db: Session, *, email: str = "renter@test.com") -> tuple[Guest, object]:
    """A Guest and UserAccount sharing the same email -- the existing link
    notify_user_by_guest_email relies on across the app."""
    user = _make_user(db, email=email)
    guest = Guest(id="G-PAYTEST", name="Renter", email=email, joined_at=date.today())
    db.add(guest)
    db.commit()
    return guest, user


def _make_obligation(db: Session, *, amount: float = 500.0) -> Obligation:
    obligation = Obligation(
        obligation_type="RENT",
        money_plane="OCCUPANCY",
        amount=amount,
        currency="INR",
        due_date=date.today(),
        status="PENDING",
    )
    db.add(obligation)
    db.commit()
    return obligation


class TestPaymentConfirmationNotifiesRenter:
    def test_confirming_a_payment_creates_a_notification_for_the_renter(self, client, db_session: Session):
        guest, user = _make_guest_linked_to_user(db_session)
        obligation = _make_obligation(db_session, amount=500.0)
        admin = _make_admin(db_session, email="finance-admin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "test-key-1"},
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
        assert r.json()["status"] == "SUCCEEDED"

        notification = db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == user.id,
                Notification.notification_type == "payment.confirmed",
            )
        )
        assert notification is not None
        assert notification.related_entity_id == str(payment_id)


class TestFullRefundMarksObligationRefunded:
    """A fully-refunded obligation must read back as REFUNDED, not PENDING --
    recompute_obligation_status previously treated a net-zero allocation sum the
    same whether nothing was ever paid or a full payment was refunded."""

    def test_refunding_the_full_amount_sets_status_refunded(self, client, db_session: Session):
        guest, _user = _make_guest_linked_to_user(db_session, email="refund-renter@test.com")
        obligation = _make_obligation(db_session, amount=500.0)
        admin = _make_admin(db_session, email="refund-admin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "test-refund-key-1"},
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

        db_session.refresh(obligation)
        assert obligation.status == "PAID"

        r = client.post(
            "/api/finance/refunds",
            json={"paymentId": payment_id, "obligationId": obligation.id, "amount": 500.0, "reason": "test"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        refund_id = r.json()["id"]

        r = client.post(f"/api/finance/refunds/{refund_id}/decide", json={"approve": True}, cookies=admin_cookies)
        assert r.status_code == 200, r.text

        db_session.refresh(obligation)
        assert obligation.status == "REFUNDED"
