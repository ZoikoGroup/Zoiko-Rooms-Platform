"""Integration tests for ZR-ENG-CLR-010 QA-Q51 "Admin tries to mark legal
liability using a service-level resolution code; blocked by authority
matrix": app/crud/disputes.py:decide_claim's new reason_category
validation against app/models/dispute_decision.py's
DISPUTE_INTERNAL_DECISION_REASON_CATEGORIES.

Covers: an internal decision with a valid, service-level reason_category
succeeds and is recorded on the DisputeDecision row; a reason_category
that isn't in the vocabulary (i.e. any attempt to express something
outside Zoiko's own service-level authority, such as a liability finding)
is refused with 400; omitting reason_category defaults to a safe,
service-level value rather than failing."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


def _open_platform_fee_case(client, renter_cookies, occupancy_id: int) -> tuple[int, int]:
    r = client.post(
        "/api/users/rentals/disputes",
        json={"occupancyId": occupancy_id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], body["claims"][0]["id"]


class TestValidReasonCategories:
    def test_a_valid_service_level_category_is_accepted_and_recorded(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="amhost1@test.com", renter_email="amrenter1@test.com")
        case_id, claim_id = _open_platform_fee_case(client, auth_user_cookie(renter), occ.id)

        admin = _make_admin(db_session, email="am-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "fee_waived", "reasonCategory": "FEE_WAIVED"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.get(f"/api/admin/disputes/{case_id}/decisions", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()[0]["reasonCategory"] == "FEE_WAIVED"

    def test_omitting_reason_category_defaults_to_a_safe_service_level_value(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="amhost2@test.com", renter_email="amrenter2@test.com")
        case_id, claim_id = _open_platform_fee_case(client, auth_user_cookie(renter), occ.id)

        admin = _make_admin(db_session, email="am-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "fee_waived"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.get(f"/api/admin/disputes/{case_id}/decisions", cookies=admin_cookies)
        assert r.json()[0]["reasonCategory"] == "OTHER_SERVICE_REASON"


class TestInvalidReasonCategoryIsBlocked:
    def test_a_category_outside_the_service_level_vocabulary_is_refused(self, client, db_session: Session):
        """The authority matrix works by construction: there is no
        vocabulary entry that could ever express a legal-liability
        finding, so any attempt to send one outside the enum is refused."""
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="amhost3@test.com", renter_email="amrenter3@test.com")
        _case_id, claim_id = _open_platform_fee_case(client, auth_user_cookie(renter), occ.id)

        admin = _make_admin(db_session, email="am-admin3@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "landlord_negligent", "reasonCategory": "LANDLORD_FOUND_NEGLIGENT"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text
