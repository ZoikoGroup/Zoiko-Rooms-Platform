"""Integration tests for the ZR-ENG-CLR-010 Section 28 operational metrics
endpoint: app/services/dispute_operational_metrics.py and the new
GET /api/analytics/dispute-operational-metrics route.

Covers: an empty dispute queue reports null rates/averages (never a
ZeroDivisionError or a fabricated 0.0), the response never claims to report
deadline breaches or time-to-triage (a deliberate Section 10 omission, not
an oversight), and a small mixed scenario produces the exact expected
counts/rates for reopen, settlement and external-referral tracking."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties
from tests.test_dispute_reopen_review import _open_and_close_platform_fee_case


def _open_deposit_case(client, renter_cookies, occupancy_id: int) -> tuple[int, int]:
    r = client.post(
        "/api/users/rentals/disputes",
        json={"occupancyId": occupancy_id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], body["claims"][0]["id"]


class TestEmptyQueue:
    def test_empty_dispute_queue_reports_nulls_not_zeros_or_errors(self, client, db_session: Session):
        admin = _make_admin(db_session, email="metrics-admin-empty@test.com", role="super_admin")
        r = client.get("/api/analytics/dispute-operational-metrics", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["totalCases"] == 0
        assert body["casesByStatus"] == {}
        assert body["casesBySeverity"] == {}
        assert body["claimsByFamily"] == {}
        assert body["claimsByAuthorityClass"] == {}
        assert body["claimsByOutcome"] == {}
        assert body["avgResolutionTimeDays"] is None
        assert body["reopenRate"] is None
        assert body["settlementRate"] is None
        assert body["externalReferralRate"] is None
        assert body["avgActiveFinancialHoldAgeDays"] is None
        assert body["avgReleasedFinancialHoldLifetimeDays"] is None

    def test_response_never_claims_to_report_deadline_or_triage_metrics(self, client, db_session: Session):
        admin = _make_admin(db_session, email="metrics-admin-fields@test.com", role="super_admin")
        r = client.get("/api/analytics/dispute-operational-metrics", cookies=auth_admin_cookie(admin))
        keys = set(r.json().keys())
        assert not any("deadline" in k.lower() or "triage" in k.lower() for k in keys)


class TestMixedScenario:
    def test_reopen_settlement_and_external_referral_rates(self, client, db_session: Session):
        admin = _make_admin(db_session, email="metrics-admin-mixed@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        # Case 1: decided A0 claim, closed, then reopened.
        _host1, renter1, occ1 = _make_occupancy_with_parties(db_session, host_email="mhost1@test.com", renter_email="mrenter1@test.com")
        renter1_cookies = auth_user_cookie(renter1)
        case1_id, claim1_id = _open_and_close_platform_fee_case(client, renter1_cookies, occ1.id, admin_cookies)
        r = client.post(
            f"/api/admin/disputes/{case1_id}/reopen", json={"grounds": "OTHER", "note": "audit"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        # Financial holds: one active, one released -- both tied to claim1.
        r = client.post(
            f"/api/admin/disputes/claims/{claim1_id}/financial-holds",
            json={"amount": 100, "authorityBasis": "test active hold"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        r = client.post(
            f"/api/admin/disputes/claims/{claim1_id}/financial-holds",
            json={"amount": 50, "authorityBasis": "test released hold"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        released_hold_id = r.json()["id"]
        r = client.post(f"/api/admin/disputes/financial-holds/{released_hold_id}/release", json={}, cookies=admin_cookies)
        assert r.status_code == 200, r.text

        # Case 2: deposit claim, settlement proposed and ACCEPTED (effective).
        host2, renter2, occ2 = _make_occupancy_with_parties(db_session, host_email="mhost2@test.com", renter_email="mrenter2@test.com")
        renter2_cookies, host2_cookies = auth_user_cookie(renter2), auth_user_cookie(host2)
        case2_id, claim2_id = _open_deposit_case(client, renter2_cookies, occ2.id)
        r = client.post(
            f"/api/users/rentals/disputes/{case2_id}/settlements",
            json={"claimIds": [claim2_id], "termsText": "Refund $300", "amount": 300, "acknowledgesNoNonwaivableWaiver": True},
            cookies=renter2_cookies,
        )
        settlement2_id = r.json()["id"]
        r = client.post(
            f"/api/users/hosting/disputes/{case2_id}/settlements/{settlement2_id}/respond",
            json={"action": "ACCEPT"}, cookies=host2_cookies,
        )
        assert r.status_code == 200, r.text

        # Case 3: deposit claim, settlement proposed and REJECTED (attempted but not effective).
        host3, renter3, occ3 = _make_occupancy_with_parties(db_session, host_email="mhost3@test.com", renter_email="mrenter3@test.com")
        renter3_cookies, host3_cookies = auth_user_cookie(renter3), auth_user_cookie(host3)
        case3_id, claim3_id = _open_deposit_case(client, renter3_cookies, occ3.id)
        r = client.post(
            f"/api/users/rentals/disputes/{case3_id}/settlements",
            json={"claimIds": [claim3_id], "termsText": "Refund $100", "amount": 100, "acknowledgesNoNonwaivableWaiver": True},
            cookies=renter3_cookies,
        )
        settlement3_id = r.json()["id"]
        r = client.post(
            f"/api/users/hosting/disputes/{case3_id}/settlements/{settlement3_id}/respond",
            json={"action": "REJECT"}, cookies=host3_cookies,
        )
        assert r.status_code == 200, r.text

        # Case 4: deposit claim, external proceeding filed (never decided).
        _host4, renter4, occ4 = _make_occupancy_with_parties(db_session, host_email="mhost4@test.com", renter_email="mrenter4@test.com")
        renter4_cookies = auth_user_cookie(renter4)
        case4_id, claim4_id = _open_deposit_case(client, renter4_cookies, occ4.id)
        r = client.post(
            f"/api/admin/disputes/{case4_id}/external-proceedings",
            json={"authorityType": "DEPOSIT_SCHEME", "claimIds": [claim4_id]},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text

        r = client.get("/api/analytics/dispute-operational-metrics", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["totalCases"] == 4
        assert body["reopenRate"] == 0.25  # 1 of 4 cases reopened
        assert body["settlementRate"] == 0.5  # 1 of 2 cases-with-a-settlement reached EFFECTIVE
        assert body["externalReferralRate"] == 0.25  # 1 of 4 cases has a filed proceeding

        # Timing-based averages aren't asserted to an exact value (test
        # runtime is nondeterministic) -- just that real data produced a
        # non-null, non-negative number instead of staying null.
        assert body["avgResolutionTimeDays"] is not None and body["avgResolutionTimeDays"] >= 0
        assert body["avgActiveFinancialHoldAgeDays"] is not None and body["avgActiveFinancialHoldAgeDays"] >= 0
        assert body["avgReleasedFinancialHoldLifetimeDays"] is not None and body["avgReleasedFinancialHoldLifetimeDays"] >= 0

        assert body["claimsByFamily"]["DEPOSIT"] == 3
        assert body["claimsByFamily"]["ZOIKO_SERVICE"] == 1
        assert body["claimsByOutcome"].get("SETTLED") == 1
