"""Integration tests for the ZR-ENG-CLR-010 Section 24 domain-event
emission added to app/api/routes/disputes.py: each mapped route now also
calls app/crud/events.py's pre-existing emit_event, writing a DomainEvent
row alongside the AuditEvent row it already wrote, sharing the same
correlation_id.

Covers: dispute.opened on case open, claim.classified on claim add,
evidence.received on upload, hold.activated on both an immediately-ACTIVE
open and a maker-checker approval, settlement.accepted + claim.resolved on
settlement acceptance, claim.referred on filing an external proceeding,
external.decision_recorded + claim.resolved on recording its decision,
claim.resolved on an internal claim decision, and dispute.closed on case
close. No consumer/dispatcher exists for these events -- this only checks
that the outbox row exists with the right shape."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.domain_event import DomainEvent
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


def _events_of_type(db_session: Session, event_type: str) -> list[DomainEvent]:
    return list(db_session.scalars(select(DomainEvent).where(DomainEvent.event_type == event_type)))


def _latest_audit_correlation_id(db_session: Session, action: str) -> str:
    row = db_session.scalars(select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.id.desc())).first()
    assert row is not None
    return row.correlation_id


class TestCaseAndClaimEvents:
    def test_opening_a_case_emits_dispute_opened(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dehost1@test.com", renter_email="derenter1@test.com")
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        case_id = r.json()["id"]

        events = _events_of_type(db_session, "dispute.opened")
        matching = [e for e in events if e.resource_id == str(case_id)]
        assert len(matching) == 1
        event = matching[0]
        assert event.resource_type == "dispute_resolution_case"
        assert event.payload["caseId"] == case_id
        assert event.actor_kind == "guest"
        assert event.correlation_id == _latest_audit_correlation_id(db_session, "dispute_case.open")

        # Adding a claim to the case is its own event.
        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/claims",
            json={"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        claim_id = r.json()["id"]

        claim_events = [e for e in _events_of_type(db_session, "claim.classified") if e.resource_id == str(claim_id)]
        assert len(claim_events) == 1
        assert claim_events[0].payload["claimFamily"] == "ZOIKO_SERVICE"

    def test_a_host_claim_carries_party_actor_kind(self, client, db_session: Session):
        host, _renter, occ = _make_occupancy_with_parties(db_session, host_email="dehost2@test.com", renter_email="derenter2@test.com")
        r = client.post(
            "/api/users/hosting/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
            cookies=auth_user_cookie(host),
        )
        assert r.status_code == 201, r.text
        case_id = r.json()["id"]

        events = [e for e in _events_of_type(db_session, "dispute.opened") if e.resource_id == str(case_id)]
        assert len(events) == 1
        assert events[0].actor_kind == "party"


class TestEvidenceEvent:
    def test_uploading_evidence_emits_evidence_received(self, client, db_session: Session, tmp_path, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "evidence_upload_dir", str(tmp_path))

        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dehost3@test.com", renter_email="derenter3@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence",
            files={"file": ("receipt.pdf", b"%PDF-1.4 evidence", "application/pdf")},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        evidence_id = r.json()["id"]

        events = [e for e in _events_of_type(db_session, "evidence.received") if e.resource_id == str(evidence_id)]
        assert len(events) == 1
        assert events[0].payload["caseId"] == case_id


class TestHoldEvent:
    def test_a_below_threshold_hold_emits_hold_activated_on_open(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dehost4@test.com", renter_email="derenter4@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 100}},
            cookies=renter_cookies,
        )
        claim_id = r.json()["claims"][0]["id"]

        admin = _make_admin(db_session, email="de-admin4@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 50, "authorityBasis": "test"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        hold_id = r.json()["id"]
        assert r.json()["status"] == "ACTIVE"

        events = [e for e in _events_of_type(db_session, "hold.activated") if e.resource_id == str(hold_id)]
        assert len(events) == 1

    def test_a_maker_checker_hold_only_emits_hold_activated_on_approval(self, client, db_session: Session, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "dispute_financial_hold_maker_checker_threshold", 1000.0)

        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dehost5@test.com", renter_email="derenter5@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 5000}},
            cookies=renter_cookies,
        )
        claim_id = r.json()["claims"][0]["id"]

        maker = _make_admin(db_session, email="de-maker5@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 5000, "authorityBasis": "large hold"}, cookies=auth_admin_cookie(maker),
        )
        hold_id = r.json()["id"]
        assert r.json()["status"] == "PROPOSED"
        assert not [e for e in _events_of_type(db_session, "hold.activated") if e.resource_id == str(hold_id)]

        checker = _make_admin(db_session, email="de-checker5@test.com", role="super_admin")
        r = client.post(f"/api/admin/disputes/financial-holds/{hold_id}/approve", cookies=auth_admin_cookie(checker))
        assert r.status_code == 200, r.text

        events = [e for e in _events_of_type(db_session, "hold.activated") if e.resource_id == str(hold_id)]
        assert len(events) == 1
        assert events[0].new_state == "ACTIVE"


class TestSettlementEvents:
    def test_accepting_a_settlement_emits_settlement_accepted_and_claim_resolved(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dehost6@test.com", renter_email="derenter6@test.com")
        renter_cookies = auth_user_cookie(renter)
        host_cookies = auth_user_cookie(host)
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
            json={"action": "ACCEPT"},
            cookies=host_cookies,
        )
        assert r.status_code == 200, r.text

        settlement_events = [e for e in _events_of_type(db_session, "settlement.accepted") if e.resource_id == str(settlement_id)]
        assert len(settlement_events) == 1
        assert settlement_events[0].actor_kind == "party"

        resolved_events = [e for e in _events_of_type(db_session, "claim.resolved") if e.resource_id == str(claim_id)]
        assert len(resolved_events) == 1
        assert resolved_events[0].payload["outcome"] == "SETTLED"

    def test_a_rejected_settlement_emits_no_settlement_or_resolution_event(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dehost7@test.com", renter_email="derenter7@test.com")
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
            json={"action": "REJECT"},
            cookies=auth_user_cookie(host),
        )
        assert r.status_code == 200, r.text

        assert not [e for e in _events_of_type(db_session, "settlement.accepted") if e.resource_id == str(settlement_id)]
        assert not [e for e in _events_of_type(db_session, "claim.resolved") if e.resource_id == str(claim_id)]


class TestExternalProceedingEvents:
    def test_filing_emits_claim_referred_and_deciding_emits_decision_recorded_and_resolved(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dehost8@test.com", renter_email="derenter8@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]

        filer = _make_admin(db_session, email="de-filer8@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "DEPOSIT_SCHEME", "claimIds": [claim_id]},
            cookies=auth_admin_cookie(filer),
        )
        assert r.status_code == 201, r.text
        proceeding_id = r.json()["id"]

        referred_events = [e for e in _events_of_type(db_session, "claim.referred") if e.resource_id == str(proceeding_id)]
        assert len(referred_events) == 1
        assert referred_events[0].payload["claimIds"] == [claim_id]

        decider = _make_admin(db_session, email="de-decider8@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/external-proceedings/{proceeding_id}/decision",
            json={"outcome": "UPHELD", "decisionDate": "2026-09-20", "finalityState": "FINAL"},
            cookies=auth_admin_cookie(decider),
        )
        assert r.status_code == 200, r.text

        decided_events = [e for e in _events_of_type(db_session, "external.decision_recorded") if e.resource_id == str(proceeding_id)]
        assert len(decided_events) == 1
        assert decided_events[0].payload["outcome"] == "UPHELD"

        resolved_events = [e for e in _events_of_type(db_session, "claim.resolved") if e.resource_id == str(claim_id)]
        assert len(resolved_events) == 1
        assert resolved_events[0].payload["proceedingId"] == proceeding_id


class TestInternalDecisionAndCloseEvents:
    def test_deciding_an_a0_claim_emits_claim_resolved(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dehost9@test.com", renter_email="derenter9@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
            cookies=renter_cookies,
        )
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]

        admin = _make_admin(db_session, email="de-admin9@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "fee_waived"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text

        events = [e for e in _events_of_type(db_session, "claim.resolved") if e.resource_id == str(claim_id)]
        assert len(events) == 1
        assert events[0].payload["outcome"] == "UPHELD"

        r = client.post(f"/api/admin/disputes/{case_id}/close", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text

        closed_events = [e for e in _events_of_type(db_session, "dispute.closed") if e.resource_id == str(case_id)]
        assert len(closed_events) == 1
        assert closed_events[0].correlation_id == _latest_audit_correlation_id(db_session, "dispute_case.close")
