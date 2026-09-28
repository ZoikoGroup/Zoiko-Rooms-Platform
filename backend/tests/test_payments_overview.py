"""Admin Payments page: Zoiko's Listing Fee revenue and the rent records
between hosts and renters, kept apart and totalled per currency."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.crud import rental_payment as rp_crud
from app.models.party import Party
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_rental_payment_legacy_bridge import _create_signed_agreement, _rental_payment_obligations_for


def _admin(db: Session, suffix: str):
    return auth_admin_cookie(_make_admin(db, email=f"po-admin-{suffix}@test.com", role="super_admin"))


class TestRentRecords:
    def test_confirmed_and_owed_amounts_are_bucketed_and_searchable(self, client, db_session: Session):
        _user, _cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="po-rent")
        obligations = _rental_payment_obligations_for(db_session, agreement_id)
        recipient = db_session.get(Party, obligations["RENT"].recipient_party_id)
        rp_crud.record_receipt_as_recipient(
            db_session, recipient, obligations["DEPOSIT"], amount=None, received_date=date.today(),
            payment_method_category="UPI",
        )
        cookies = _admin(db_session, "rent")

        r = client.get("/api/finance/payments-overview/rent", cookies=cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        deposit = float(obligations["DEPOSIT"].amount)
        assert body["summary"]["confirmed"]["count"] == 1
        assert body["summary"]["confirmed"]["totals"] == [{"currency": obligations["DEPOSIT"].currency, "amount": deposit}]
        assert {row["obligationId"] for row in body["rows"]} >= {obligations["RENT"].id, obligations["DEPOSIT"].id}

        only_confirmed = client.get("/api/finance/payments-overview/rent?bucket=confirmed", cookies=cookies).json()["rows"]
        assert [row["obligationId"] for row in only_confirmed] == [obligations["DEPOSIT"].id]

        listing_name = only_confirmed[0]["listingName"]
        assert listing_name
        found = client.get(f"/api/finance/payments-overview/rent?q={listing_name[:4]}", cookies=cookies).json()["rows"]
        assert obligations["RENT"].id in {row["obligationId"] for row in found}
        assert client.get("/api/finance/payments-overview/rent?q=zz-no-such-thing", cookies=cookies).json()["rows"] == []

    def test_an_unknown_bucket_is_refused(self, client, db_session: Session):
        r = client.get("/api/finance/payments-overview/rent?bucket=collected", cookies=_admin(db_session, "bad"))
        assert r.status_code == 400


class TestListingFeeRevenue:
    def test_revenue_totals_come_back_per_currency(self, client, db_session: Session):
        r = client.get("/api/finance/payments-overview/listing-fees", cookies=_admin(db_session, "rev"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"collected", "refunded", "paidCount", "pendingCount", "failedCount"}


class TestAccess:
    def test_only_super_admins_see_it(self, client, db_session: Session):
        user = _make_user(db_session, email="po-user@test.com")
        db_session.commit()
        assert client.get("/api/finance/payments-overview/rent", cookies=auth_user_cookie(user)).status_code in (401, 403)
        plain = _make_admin(db_session, email="po-plain@test.com", role="admin")
        assert client.get("/api/finance/payments-overview/rent", cookies=auth_admin_cookie(plain)).status_code == 403
