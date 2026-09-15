"""ZR-ENG-CLR-005 AC-11: 'A payer sees complete amount-due-now line items and
future schedule before charge confirmation.' Tests
crud/finance.py::get_payment_preview via the renter-facing
GET /api/users/rentals/agreements/{id}/payment-preview route -- amount_due_now
reads real, currently-unpaid Obligation rows (rent + deposit; this build has
no renter-side fee/tax/credit line item to add); future_schedule is a pure
projection from the agreement's PaymentSchedule, creating no Obligation rows."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_application_workflow import _make_verified_renter_with_published_listing
from tests.test_deposit_claims import _make_agreement_eligible


def _create_signed_agreement(client, db_session: Session, *, email_suffix: str, monthly_rent: float = 3000.0, term_months: int = 6):
    user, listing_id = _make_verified_renter_with_published_listing(db_session, email=f"preview-renter-{email_suffix}@test.com")
    user_cookies = auth_user_cookie(user)
    admin = _make_admin(db_session, email=f"preview-admin-{email_suffix}@test.com", role="super_admin")
    admin_cookies = auth_admin_cookie(admin)

    r = client.post(
        "/api/users/rentals/applications",
        json={"listingId": listing_id, "message": "hi", "desiredMoveIn": None},
        cookies=user_cookies,
    )
    assert r.status_code == 201, r.text
    application_id = r.json()["id"]
    assert client.post(
        f"/api/leasing/applications/{application_id}/decide", json={"decision": "APPROVED"}, cookies=admin_cookies,
    ).status_code == 200
    r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
    offer_id = r.json()["id"]
    start_date = date.today() + timedelta(days=5)
    r = client.post(
        f"/api/leasing/offers/{offer_id}/terms",
        json={
            "monthlyRent": monthly_rent, "depositAmount": monthly_rent,
            "startDate": start_date.isoformat(), "termMonths": term_months,
        },
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text
    assert client.post(f"/api/leasing/offers/{offer_id}/send", cookies=admin_cookies).status_code == 200
    assert client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=user_cookies).status_code == 200

    _make_agreement_eligible(db_session, listing_id)
    r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    agreement_id = r.json()["id"]
    return user, user_cookies, agreement_id, start_date


class TestPaymentPreview:
    def test_amount_due_now_includes_rent_and_deposit(self, client, db_session: Session):
        user, user_cookies, agreement_id, start_date = _create_signed_agreement(client, db_session, email_suffix="basic1")

        r = client.get(f"/api/users/rentals/agreements/{agreement_id}/payment-preview", cookies=user_cookies)
        assert r.status_code == 200, r.text
        body = r.json()

        due_now_types = sorted(item["obligationType"] for item in body["amountDueNow"])
        assert due_now_types == ["DEPOSIT", "RENT"]
        for item in body["amountDueNow"]:
            assert item["status"] == "PENDING"
            assert float(item["amount"]) == 3000.0

    def test_future_schedule_projects_remaining_monthly_periods(self, client, db_session: Session):
        _user, user_cookies, agreement_id, start_date = _create_signed_agreement(
            client, db_session, email_suffix="future1", term_months=6,
        )

        r = client.get(f"/api/users/rentals/agreements/{agreement_id}/payment-preview", cookies=user_cookies)
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["cadence"] == "MONTHLY"
        # 6 more monthly periods follow the initial rent obligation before the
        # 6-month term ends -- same due-date boundary rule as
        # crud/occupancy.py:generate_next_rent_obligation (due date == the
        # term end is still generated, only strictly past it stops).
        assert body["remainingScheduledCount"] == 6
        assert len(body["futureSchedule"]) == 6
        due_dates = [item["dueDate"] for item in body["futureSchedule"]]
        assert due_dates == sorted(due_dates)
        for item in body["futureSchedule"]:
            assert float(item["amount"]) == 3000.0
            assert item["cadence"] == "MONTHLY"

    def test_a_different_renter_cannot_preview_it(self, client, db_session: Session):
        _user, _user_cookies, agreement_id, _start_date = _create_signed_agreement(client, db_session, email_suffix="owner1")
        stranger = _make_user(db_session, email="preview-stranger@test.com")

        r = client.get(f"/api/users/rentals/agreements/{agreement_id}/payment-preview", cookies=auth_user_cookie(stranger))
        assert r.status_code == 403, r.text

    def test_paying_rent_removes_it_from_amount_due_now(self, client, db_session: Session):
        _user, user_cookies, agreement_id, _start_date = _create_signed_agreement(client, db_session, email_suffix="paid1")
        admin = _make_admin(db_session, email="preview-payadmin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.get(f"/api/users/rentals/agreements/{agreement_id}/payment-preview", cookies=user_cookies)
        rent_obligation = next(o for o in r.json()["amountDueNow"] if o["obligationType"] == "RENT")

        guest_id = r.json()["amountDueNow"][0]["guestId"]
        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest_id, "amount": 3000.0, "currency": "INR", "idempotencyKey": "preview-pay-1"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": rent_obligation["id"], "amount": 3000.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.get(f"/api/users/rentals/agreements/{agreement_id}/payment-preview", cookies=user_cookies)
        assert r.status_code == 200, r.text
        due_now_types = sorted(item["obligationType"] for item in r.json()["amountDueNow"])
        assert due_now_types == ["DEPOSIT"]
