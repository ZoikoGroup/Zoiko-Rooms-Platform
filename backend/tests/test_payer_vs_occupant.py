"""ZR-ENG-CLR-005 AC-03/Section 4.1: the occupant (guest_id) and the payer are
not always the same person. Covers the three payer shapes create_payment_intent
supports -- unspecified (payer == occupant, unchanged default), a registered
guest paying on someone else's behalf, and a non-registered payer identified by
name/email/phone -- plus idempotency-key collision detection now accounting for
payer identity."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.guest import Guest
from tests.conftest import auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


class TestPayerDefaultsToOccupant:
    def test_no_payer_specified_defaults_to_occupant(self, client, db_session: Session):
        _obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="payer-default")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "payer-default-1"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["payerGuestId"] == guest.id
        assert body["payerName"] is None
        assert body["payerDisplayName"] == guest.name


class TestThirdPartyRegisteredGuestPayer:
    def test_registered_guest_can_pay_for_a_different_occupant(self, client, db_session: Session):
        _obligation, admin, occupant, _party_id = _make_provider_rent_obligation(db_session, suffix="payer-3p")
        admin_cookies = auth_admin_cookie(admin)

        payer_guest = Guest(id="G-PAYER-PARENT", name="Occupant's Parent", email="parent@test.com", joined_at=date.today())
        db_session.add(payer_guest)
        db_session.commit()

        r = client.post(
            "/api/finance/payments",
            json={
                "guestId": occupant.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "payer-3p-1",
                "payerGuestId": payer_guest.id,
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["guestId"] == occupant.id
        assert body["payerGuestId"] == payer_guest.id
        assert body["payerDisplayName"] == payer_guest.name

    def test_unknown_payer_guest_id_is_rejected(self, client, db_session: Session):
        _obligation, admin, occupant, _party_id = _make_provider_rent_obligation(db_session, suffix="payer-3p-404")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={
                "guestId": occupant.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "payer-3p-404-1",
                "payerGuestId": "G-DOES-NOT-EXIST",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 404


class TestNonRegisteredPayer:
    def test_payer_email_without_a_name_is_rejected(self, client, db_session: Session):
        _obligation, admin, occupant, _party_id = _make_provider_rent_obligation(db_session, suffix="payer-noname")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={
                "guestId": occupant.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "payer-noname-1",
                "payerEmail": "someone@test.com",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 400

    def test_non_registered_payer_with_a_name_is_recorded(self, client, db_session: Session):
        _obligation, admin, occupant, _party_id = _make_provider_rent_obligation(db_session, suffix="payer-external")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={
                "guestId": occupant.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "payer-external-1",
                "payerName": "Occupant's Employer", "payerEmail": "billing@employer.test.com",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["payerGuestId"] is None
        assert body["payerName"] == "Occupant's Employer"
        assert body["payerEmail"] == "billing@employer.test.com"
        assert body["payerDisplayName"] == "Occupant's Employer"


class TestIdempotencyAccountsForPayer:
    def test_same_key_with_a_different_payer_is_a_conflict(self, client, db_session: Session):
        _obligation, admin, occupant, _party_id = _make_provider_rent_obligation(db_session, suffix="payer-idem")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": occupant.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "payer-idem-1"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text

        r = client.post(
            "/api/finance/payments",
            json={
                "guestId": occupant.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "payer-idem-1",
                "payerName": "Someone Else",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 409

    def test_same_key_and_same_payer_is_a_true_retry(self, client, db_session: Session):
        _obligation, admin, occupant, _party_id = _make_provider_rent_obligation(db_session, suffix="payer-idem-retry")
        admin_cookies = auth_admin_cookie(admin)

        payload = {"guestId": occupant.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "payer-idem-retry-1"}
        r1 = client.post("/api/finance/payments", json=payload, cookies=admin_cookies)
        r2 = client.post("/api/finance/payments", json=payload, cookies=admin_cookies)
        assert r1.status_code == 201, r1.text
        assert r2.status_code == 201, r2.text
        assert r1.json()["id"] == r2.json()["id"]
