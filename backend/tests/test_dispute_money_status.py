"""Integration tests for ZR-ENG-CLR-010 AC-32/Section 11 "Money status":
app/crud/disputes.py's new compute_money_status, exposed on
DisputeCaseRead via the renter/host/admin single-case GET/mutation routes
in app/api/routes/disputes.py (_to_case_read).

Covers: a still-open claim counts as amount_disputed; an ACTIVE financial
hold counts as amount_held; a claim settled via bilateral settlement
counts as amount_settled; a claim decided NOT_UPHELD with no hold ever
placed counts as amount_undisputed; renter, host and admin case views all
expose the same money_status."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


def _bucket(money_status: list[dict], currency: str = "INR") -> dict:
    return next(b for b in money_status if b["currency"] == currency)


class TestDisputedAndHeld:
    def test_an_open_claim_is_disputed_and_an_active_hold_is_held(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="mshost1@test.com", renter_email="msrenter1@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]
        assert _bucket(r.json()["moneyStatus"])["amountDisputed"] == 600.0

        admin = _make_admin(db_session, email="ms-admin1@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 400, "authorityBasis": "test"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text

        r = client.get(f"/api/users/rentals/disputes/{case_id}", cookies=renter_cookies)
        bucket = _bucket(r.json()["moneyStatus"])
        assert bucket["amountDisputed"] == 600.0
        assert bucket["amountHeld"] == 400.0
        assert bucket["amountSettled"] == 0.0
        assert bucket["amountUndisputed"] == 0.0

    def test_the_host_and_admin_views_expose_the_same_money_status(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="mshost2@test.com", renter_email="msrenter2@test.com")
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        r = client.get(f"/api/users/hosting/disputes/{case_id}", cookies=auth_user_cookie(host))
        assert _bucket(r.json()["moneyStatus"])["amountDisputed"] == 600.0

        admin = _make_admin(db_session, email="ms-admin2@test.com", role="super_admin")
        r = client.get(f"/api/admin/disputes/{case_id}", cookies=auth_admin_cookie(admin))
        assert _bucket(r.json()["moneyStatus"])["amountDisputed"] == 600.0


class TestSettledAndUndisputed:
    def test_a_settled_claim_counts_as_amount_settled(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="mshost3@test.com", renter_email="msrenter3@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements",
            json={"claimIds": [claim_id], "termsText": "Refund $300", "amount": 300, "acknowledgesNoNonwaivableWaiver": True},
            cookies=renter_cookies,
        )
        settlement_id = r.json()["id"]
        r = client.post(
            f"/api/users/hosting/disputes/{case_id}/settlements/{settlement_id}/respond",
            json={"action": "ACCEPT"}, cookies=auth_user_cookie(host),
        )
        assert r.status_code == 200, r.text

        r = client.get(f"/api/users/rentals/disputes/{case_id}", cookies=renter_cookies)
        bucket = _bucket(r.json()["moneyStatus"])
        assert bucket["amountSettled"] == 600.0
        assert bucket["amountDisputed"] == 0.0
        assert bucket["amountUndisputed"] == 0.0

    def test_a_not_upheld_claim_with_no_hold_ever_placed_is_undisputed(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="mshost4@test.com", renter_email="msrenter4@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
            cookies=renter_cookies,
        )
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]

        admin = _make_admin(db_session, email="ms-admin4@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "NOT_UPHELD", "reasonCode": "no_basis"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text

        r = client.get(f"/api/users/rentals/disputes/{case_id}", cookies=renter_cookies)
        bucket = _bucket(r.json()["moneyStatus"])
        assert bucket["amountUndisputed"] == 50.0
        assert bucket["amountDisputed"] == 0.0
        assert bucket["amountSettled"] == 0.0
