"""Integration tests for the ZR-ENG-CLR-010 Section 19/22/26 append-only
DisputeDecision history: app/models/dispute_decision.py,
app/crud/disputes.py's new record_dispute_decision/list_decisions_for_case,
and the /decisions read route added to app/api/routes/disputes.py.

Covers: a first internal decision writes one ADMIN_INTERNAL_DECISION row;
a re-decision after review/reopen APPENDS a second row rather than losing
the first (the gap this object exists to close -- claim.decided_at/outcome
are overwritten in place, but the decision history is not); an external
proceeding's decision and a DISMISSED status update each write their own
EXTERNAL_PROCEEDING_* row; an accepted settlement writes a SETTLEMENT_ACCEPTED
row; the case export's chronology surfaces every decision, not just the
current one."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


class TestAdminInternalDecision:
    def test_a_decision_writes_one_decision_row(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dd-host1@test.com", renter_email="dd-renter1@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
            cookies=renter_cookies,
        )
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]

        admin = _make_admin(db_session, email="dd-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "fee_waived"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.get(f"/api/admin/disputes/{case_id}/decisions", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        decisions = r.json()
        assert len(decisions) == 1
        assert decisions[0]["claimId"] == claim_id
        assert decisions[0]["outcome"] == "UPHELD"
        assert decisions[0]["decisionBasis"] == "ADMIN_INTERNAL_DECISION"
        assert decisions[0]["authority"] == "admin"
        assert decisions[0]["decidedByAdminId"] == admin.id

    def test_a_re_decision_after_review_appends_rather_than_replaces_the_history(self, client, db_session: Session):
        """The gap this object exists to close: claim.decided_at/outcome
        get overwritten in place by a re-decision, but the decision history
        must still show both the original and the revised outcome."""
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dd-host2@test.com", renter_email="dd-renter2@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
            cookies=renter_cookies,
        )
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]

        admin = _make_admin(db_session, email="dd-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "NOT_UPHELD", "reasonCode": "insufficient_evidence"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/claims/{claim_id}/request-review",
            json={"reason": "New evidence"}, cookies=renter_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "reviewed_and_reversed"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        # The claim's own flattened columns now only show the latest outcome...
        assert r.json()["outcome"] == "UPHELD"

        # ...but the append-only history still has both.
        r = client.get(f"/api/admin/disputes/{case_id}/decisions", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        decisions = r.json()
        assert len(decisions) == 2
        assert decisions[0]["outcome"] == "NOT_UPHELD"
        assert decisions[0]["reasonCode"] == "insufficient_evidence"
        assert decisions[1]["outcome"] == "UPHELD"
        assert decisions[1]["reasonCode"] == "reviewed_and_reversed"
        assert decisions[0]["decidedAt"] <= decisions[1]["decidedAt"]

        # The case export's chronology reflects both decisions too.
        r = client.get(f"/api/admin/disputes/{case_id}/export", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        decided_events = [e for e in r.json()["chronology"] if e["eventType"] == "claim.decided"]
        assert len(decided_events) == 2


class TestExternalProceedingDecisions:
    def test_a_recorded_decision_writes_an_external_proceeding_decided_row(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dd-host3@test.com", renter_email="dd-renter3@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]

        filer = _make_admin(db_session, email="dd-filer3@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "DEPOSIT_SCHEME", "claimIds": [claim_id]},
            cookies=auth_admin_cookie(filer),
        )
        proceeding_id = r.json()["id"]

        decider = _make_admin(db_session, email="dd-decider3@test.com", role="super_admin")
        decider_cookies = auth_admin_cookie(decider)
        r = client.post(
            f"/api/admin/disputes/external-proceedings/{proceeding_id}/decision",
            json={"outcome": "UPHELD", "decisionDate": "2026-09-20", "finalityState": "FINAL", "reasonCode": "scheme_ruled_for_renter"},
            cookies=decider_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.get(f"/api/admin/disputes/{case_id}/decisions", cookies=decider_cookies)
        decisions = r.json()
        assert len(decisions) == 1
        assert decisions[0]["decisionBasis"] == "EXTERNAL_PROCEEDING_DECIDED"
        assert decisions[0]["authority"] == "external_proceeding"
        assert decisions[0]["externalProceedingId"] == proceeding_id
        assert decisions[0]["decidedByAdminId"] == decider.id

    def test_a_dismissed_status_update_writes_an_external_proceeding_dismissed_row(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dd-host4@test.com", renter_email="dd-renter4@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]

        admin = _make_admin(db_session, email="dd-admin4@test.com", role="super_admin")
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

        r = client.get(f"/api/admin/disputes/{case_id}/decisions", cookies=admin_cookies)
        decisions = r.json()
        assert len(decisions) == 1
        assert decisions[0]["decisionBasis"] == "EXTERNAL_PROCEEDING_DISMISSED"
        assert decisions[0]["outcome"] == "NOT_UPHELD"


class TestSettlementDecision:
    def test_an_accepted_settlement_writes_a_settlement_accepted_row(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dd-host5@test.com", renter_email="dd-renter5@test.com")
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

        admin = _make_admin(db_session, email="dd-admin5@test.com", role="super_admin")
        r = client.get(f"/api/admin/disputes/{case_id}/decisions", cookies=auth_admin_cookie(admin))
        decisions = r.json()
        assert len(decisions) == 1
        assert decisions[0]["decisionBasis"] == "SETTLEMENT_ACCEPTED"
        assert decisions[0]["authority"] == "settlement"
        assert decisions[0]["settlementId"] == settlement_id
        assert decisions[0]["outcome"] == "SETTLED"
