"""Integration tests for the ZR-ENG-CLR-010 Section 24 external proceeding
service: app/models/dispute_external_proceeding.py,
app/crud/dispute_external_proceeding.py and the /external-proceedings
routes added to app/api/routes/disputes.py.

Covers: filing moves a claim to EXTERNAL_REFERRAL (and refuses A0 claims,
AC-6's mirror image), the QA-Q19 threshold-gated dual-control
filer-cannot-decide guard (mandatory at/above the configured amount or for
a SEV-0 case, not unconditional), recording a decision resolves the claim
(and the case), DISMISSED/WITHDRAWN resolve claims without a full decision
record, and renter/host read scoping."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import settings
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


def _open_deposit_case(client, renter_cookies, *, amount: float = 600) -> tuple[int, int]:
    r = client.post(
        "/api/users/rentals/disputes",
        json={"claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": amount}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], body["claims"][0]["id"]


def _open_platform_fee_case(client, renter_cookies) -> tuple[int, int]:
    r = client.post(
        "/api/users/rentals/disputes",
        json={"claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], body["claims"][0]["id"]


class TestFiling:
    def test_filing_moves_the_claim_to_external_referral(self, client, db_session: Session):
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="ephost1@test.com", renter_email="eprenter1@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies)

        admin = _make_admin(db_session, email="ep-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "DEPOSIT_SCHEME", "authorityName": "State Deposit Scheme", "claimIds": [claim_id]},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "FILED"
        assert body["claimIds"] == [claim_id]

        r = client.get(f"/api/users/rentals/disputes/{case_id}", cookies=renter_cookies)
        assert r.json()["claims"][0]["status"] == "EXTERNAL_REFERRAL"

    def test_cannot_file_a_proceeding_for_an_a0_zoiko_controlled_claim(self, client, db_session: Session):
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="ephost2@test.com", renter_email="eprenter2@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_platform_fee_case(client, renter_cookies)

        admin = _make_admin(db_session, email="ep-admin2@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "TRIBUNAL_COURT", "claimIds": [claim_id]},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 409, r.text


class TestDecisionRecording:
    def test_the_filing_admin_cannot_also_record_the_decision_above_the_threshold(self, client, db_session: Session, monkeypatch):
        monkeypatch.setattr(settings, "dispute_external_proceeding_dual_control_threshold", 1000.0)
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="ephost3@test.com", renter_email="eprenter3@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, amount=5000)

        admin = _make_admin(db_session, email="ep-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "DEPOSIT_SCHEME", "claimIds": [claim_id]},
            cookies=admin_cookies,
        )
        proceeding_id = r.json()["id"]

        r = client.post(
            f"/api/admin/disputes/external-proceedings/{proceeding_id}/decision",
            json={"outcome": "UPHELD", "decisionDate": "2026-09-20", "finalityState": "FINAL"},
            cookies=admin_cookies,
        )
        assert r.status_code == 409, r.text

    def test_below_every_trigger_the_filing_admin_may_also_record_the_decision(self, client, db_session: Session, monkeypatch):
        """QA-Q19: dual control is threshold-gated, not unconditional -- a
        small, non-safety-flagged claim's filer may also record its
        proceeding's decision."""
        monkeypatch.setattr(settings, "dispute_external_proceeding_dual_control_threshold", 10000.0)
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="ephost3b@test.com", renter_email="eprenter3b@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, amount=600)

        admin = _make_admin(db_session, email="ep-admin3b@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "DEPOSIT_SCHEME", "claimIds": [claim_id]},
            cookies=admin_cookies,
        )
        proceeding_id = r.json()["id"]

        r = client.post(
            f"/api/admin/disputes/external-proceedings/{proceeding_id}/decision",
            json={"outcome": "UPHELD", "decisionDate": "2026-09-20", "finalityState": "FINAL"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "DECIDED"

    def test_a_sev0_case_requires_dual_control_regardless_of_amount(self, client, db_session: Session):
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="ephost3c@test.com", renter_email="eprenter3c@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"claim": {"claimCode": "ILLEGAL_LOCKOUT", "claimFamily": "SUBLET_OCCUPANCY", "safetyFlag": True}},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        case_id, claim_id = body["id"], body["claims"][0]["id"]
        assert body["severity"] == "SEV-0"

        admin = _make_admin(db_session, email="ep-admin3c@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "TRIBUNAL_COURT", "claimIds": [claim_id]},
            cookies=admin_cookies,
        )
        proceeding_id = r.json()["id"]

        r = client.post(
            f"/api/admin/disputes/external-proceedings/{proceeding_id}/decision",
            json={"outcome": "UPHELD", "decisionDate": "2026-09-20", "finalityState": "FINAL"},
            cookies=admin_cookies,
        )
        assert r.status_code == 409, r.text

    def test_a_different_admin_records_a_decision_and_resolves_the_claim_and_case(self, client, db_session: Session):
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="ephost4@test.com", renter_email="eprenter4@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies)

        filer = _make_admin(db_session, email="ep-filer4@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "DEPOSIT_SCHEME", "claimIds": [claim_id]},
            cookies=auth_admin_cookie(filer),
        )
        proceeding_id = r.json()["id"]

        decider = _make_admin(db_session, email="ep-decider4@test.com", role="super_admin")
        decider_cookies = auth_admin_cookie(decider)
        r = client.post(
            f"/api/admin/disputes/external-proceedings/{proceeding_id}/decision",
            json={"outcome": "UPHELD", "decisionDate": "2026-09-20", "finalityState": "FINAL", "reasonCode": "scheme_ruled_for_renter"},
            cookies=decider_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "DECIDED"
        assert body["finalityState"] == "FINAL"

        r = client.get(f"/api/users/rentals/disputes/{case_id}", cookies=renter_cookies)
        case = r.json()
        assert case["claims"][0]["status"] == "UPHELD"
        assert case["status"] == "RESOLVED"

    def test_dismissed_status_resolves_the_linked_claim_to_not_upheld(self, client, db_session: Session):
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="ephost5@test.com", renter_email="eprenter5@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies)

        admin = _make_admin(db_session, email="ep-admin5@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "DEPOSIT_SCHEME", "claimIds": [claim_id]},
            cookies=admin_cookies,
        )
        proceeding_id = r.json()["id"]

        r = client.post(
            f"/api/admin/disputes/external-proceedings/{proceeding_id}/status", json={"status": "DISMISSED"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "DISMISSED"

        r = client.get(f"/api/users/rentals/disputes/{case_id}", cookies=renter_cookies)
        assert r.json()["claims"][0]["status"] == "NOT_UPHELD"


class TestReadScoping:
    def test_host_cannot_read_external_proceedings_on_a_case_they_do_not_own(self, client, db_session: Session):
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="ephost6@test.com", renter_email="eprenter6@test.com")
        case_id, _claim_id = _open_deposit_case(client, auth_user_cookie(renter))

        intruder_host = _make_user(db_session, email="intruder-ep-host@test.com")
        r = client.get(f"/api/users/hosting/disputes/{case_id}/external-proceedings", cookies=auth_user_cookie(intruder_host))
        assert r.status_code in (400, 403), r.text
