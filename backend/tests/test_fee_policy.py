"""ZR-PAY-CFG-001 Decision 3 / PAY-CFG-04: Zoiko Rooms takes no commission on
rent. Replaces the earlier ZR-ENG-CLR-005 AC-09 tests that proved a
percentage-of-rent platform fee was resolved from the market policy pack --
that fee no longer exists. Even a legacy platform_fee_rate value left in
the database must have no effect, and no API may set one."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.market_policy import MarketPolicyPack
from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


def _pay_and_run_payout(client, db_session: Session, *, suffix: str) -> dict:
    obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix=suffix, amount=1000.0)
    admin_cookies = auth_admin_cookie(admin)
    r = client.post(
        "/api/finance/payments",
        json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": f"fee-{suffix}"},
        cookies=admin_cookies,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"/api/finance/payments/{r.json()['id']}/confirm",
        json={"allocations": [{"obligationId": obligation.id, "amount": 1000.0}]},
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies)
    assert r.status_code == 200, r.text
    return r.json()


class TestNoRentalCommission:
    def test_payout_takes_no_fee(self, client, db_session: Session):
        assert _pay_and_run_payout(client, db_session, suffix="nofee1")["amount"] == 1000.0

    def test_a_legacy_rate_left_in_the_database_is_ignored(self, client, db_session: Session):
        policy = db_session.query(MarketPolicyPack).filter_by(jurisdiction_code="IN").one()
        policy.platform_fee_rate = 0.20
        db_session.commit()
        assert _pay_and_run_payout(client, db_session, suffix="nofee2")["amount"] == 1000.0

    def test_no_api_can_set_a_commission(self, client, db_session: Session):
        admin = _make_admin(db_session, email="nofee-admin@test.com", role="super_admin")
        db_session.commit()
        r = client.post(
            "/api/market-policy-packs",
            json={"jurisdictionCode": "FR", "effectiveFrom": "2026-01-01", "platformFeeRate": 0.10},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 422, r.text

    def test_policy_pack_reports_no_commission(self, client, db_session: Session):
        admin = _make_admin(db_session, email="nofee-admin2@test.com", role="super_admin")
        db_session.commit()
        r = client.get("/api/market-policy-packs", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert all(pack["platformFeeRate"] is None for pack in r.json())
