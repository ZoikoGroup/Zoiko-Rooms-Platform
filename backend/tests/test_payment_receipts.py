"""ZR-ENG-CLR-005 Section 13.1/13.2, AC-25: confirm_payment generates an
immutable PaymentReceipt best-effort; the admin and renter download routes
both serve the same rendered PDF; the renter route is ownership-checked;
regenerating (idempotency) never creates a second row or different bytes."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import PaymentReceipt
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_ledger import _make_provider_rent_obligation


def _confirm_a_payment(client, db_session: Session, *, suffix: str, amount: float = 500.0):
    obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix=suffix, amount=amount)
    admin_cookies = auth_admin_cookie(admin)

    r = client.post(
        "/api/finance/payments",
        json={"guestId": guest.id, "amount": amount, "currency": "INR", "idempotencyKey": f"receipt-{suffix}"},
        cookies=admin_cookies,
    )
    assert r.status_code == 201, r.text
    payment_id = r.json()["id"]
    r = client.post(
        f"/api/finance/payments/{payment_id}/confirm",
        json={"allocations": [{"obligationId": obligation.id, "amount": amount}]},
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text
    return payment_id, admin, admin_cookies, guest


class TestConfirmPaymentGeneratesAReceipt:
    def test_confirming_a_payment_creates_exactly_one_receipt(self, client, db_session: Session):
        payment_id, _admin, _admin_cookies, _guest = _confirm_a_payment(client, db_session, suffix="gen1")

        receipts = db_session.scalars(select(PaymentReceipt).where(PaymentReceipt.payment_id == payment_id)).all()
        assert len(receipts) == 1
        assert receipts[0].receipt_number == f"RCPT-{payment_id:08d}"


class TestAdminReceiptDownload:
    def test_admin_can_download_the_receipt_pdf(self, client, db_session: Session):
        payment_id, _admin, admin_cookies, _guest = _confirm_a_payment(client, db_session, suffix="admin1")

        r = client.get(f"/api/finance/payments/{payment_id}/receipt", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/pdf"
        assert len(r.content) > 0
        assert r.content.startswith(b"%PDF")
        assert "attachment" in r.headers["content-disposition"]

    def test_calling_it_twice_is_idempotent(self, client, db_session: Session):
        payment_id, _admin, admin_cookies, _guest = _confirm_a_payment(client, db_session, suffix="idem1")

        r1 = client.get(f"/api/finance/payments/{payment_id}/receipt", cookies=admin_cookies)
        r2 = client.get(f"/api/finance/payments/{payment_id}/receipt", cookies=admin_cookies)
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.content == r2.content

        receipts = db_session.scalars(select(PaymentReceipt).where(PaymentReceipt.payment_id == payment_id)).all()
        assert len(receipts) == 1

    def test_other_providers_admin_cannot_download_it(self, client, db_session: Session):
        payment_id, _admin, _admin_cookies, _guest = _confirm_a_payment(client, db_session, suffix="otherprov1")
        outsider = _make_admin(db_session, email="receipt-outsider@test.com", role="admin")

        r = client.get(f"/api/finance/payments/{payment_id}/receipt", cookies=auth_admin_cookie(outsider))
        assert r.status_code == 403, r.text

    def test_receipt_not_available_for_a_payment_that_never_succeeded(self, client, db_session: Session):
        admin = _make_admin(db_session, email="receipt-pending-admin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        _obligation, _admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="pending1")

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "receipt-pending-1"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        payment_id = r.json()["id"]

        r = client.get(f"/api/finance/payments/{payment_id}/receipt", cookies=admin_cookies)
        assert r.status_code == 409, r.text


class TestUserReceiptDownload:
    def test_owning_renter_can_download_their_receipt(self, client, db_session: Session):
        payment_id, _admin, _admin_cookies, guest = _confirm_a_payment(client, db_session, suffix="user1")
        renter = _make_user(db_session, email="receipt-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.get(f"/api/users/payments/{payment_id}/receipt", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.content.startswith(b"%PDF")

    def test_a_different_renter_cannot_download_it(self, client, db_session: Session):
        payment_id, _admin, _admin_cookies, guest = _confirm_a_payment(client, db_session, suffix="user2")
        owning_renter = _make_user(db_session, email="receipt-owner@test.com")
        guest.user_account_id = owning_renter.id
        db_session.commit()

        other_renter = _make_user(db_session, email="receipt-stranger@test.com")
        r = client.get(f"/api/users/payments/{payment_id}/receipt", cookies=auth_user_cookie(other_renter))
        assert r.status_code == 403, r.text
