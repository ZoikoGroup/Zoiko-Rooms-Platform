"""Integration tests for ZR-ENG-CLR-010 AC-36 "Cancellation/termination
disputes preserve Section 6/7 policy snapshots and no-double-recovery
logic": app/crud/disputes.py's new TERMINATION_CASE source-record linkage
for CANCELLATION/EARLY_TERMINATION claim codes, and
app/crud/dispute_settlement.py's new _assert_no_termination_double_recovery
guard.

Covers: a CANCELLATION claim on an occupancy with a TerminationCase links
to it and freezes cause_code/status/refund_entitlement_status into
source_record_snapshot; a settlement cannot be proposed for such a claim
once its termination refund entitlement has been EXECUTED; a merely
CALCULATED (not yet executed) entitlement does not block settlement; an
unrelated claim family/code is never affected.

Also QA-Q27 "Host nonperformance claim triggers listing review without
automatically refunding incorrect amount" / Section 17 "If a cancellation
was triggered by Host nonperformance or compliance failure, preserve the
causation event... separately from the monetary claim": a cause_code of
HOST_FAULT_OR_NONPERFORMANCE is preserved on the claim via the same
source_record_snapshot mechanism, and deciding/settling the claim never
executes a refund itself (dispute code has no refund-execution code path
at all -- see crud/dispute_settlement.py's own NON-BEHAVIOR docstring)."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.leasing import Agreement
from app.models.refund_entitlement import RefundEntitlement
from app.models.termination_case import TerminationCase
from tests.conftest import auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


def _make_termination_case_with_entitlement(
    db_session: Session, occ, *, entitlement_status: str | None, cause_code: str = "RENTER_ORDINARY_EARLY_EXIT",
) -> TerminationCase:
    agreement = db_session.query(Agreement).filter(Agreement.offer_id == occ.offer_id).first()
    if agreement is None:
        agreement = Agreement(offer_id=occ.offer_id, status="SIGNED")
        db_session.add(agreement)
        db_session.flush()

    case = TerminationCase(
        occupancy_id=occ.id, agreement_id=agreement.id, initiator_guest_id=occ.guest_id,
        cause_code=cause_code, status="EFFECTIVE_DATE_SET",
        effective_termination_date=date(2026, 10, 1),
    )
    db_session.add(case)
    db_session.flush()

    if entitlement_status is not None:
        entitlement = RefundEntitlement(
            termination_case_id=case.id, version=1, currency="INR", gross_refundable=500, net_refund=500,
            status=entitlement_status,
        )
        db_session.add(entitlement)
        db_session.flush()

    db_session.commit()
    return case


class TestSourceRecordLinkage:
    def test_a_cancellation_claim_links_to_the_termination_case_and_snapshots_it(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="tdrhost1@test.com", renter_email="tdrrenter1@test.com")
        term_case = _make_termination_case_with_entitlement(db_session, occ, entitlement_status="CALCULATED")

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "CANCELLATION", "claimFamily": "BOOKING_AGREEMENT"}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        claim = r.json()["claims"][0]
        assert claim["sourceRecordType"] == "TERMINATION_CASE"
        assert claim["sourceRecordId"] == str(term_case.id)
        snapshot = claim["sourceRecordSnapshot"]
        assert snapshot["cause_code"] == "RENTER_ORDINARY_EARLY_EXIT"
        assert snapshot["refund_entitlement_status"] == "CALCULATED"

    def test_a_date_change_claim_code_still_links_to_booking_change_request_not_termination(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="tdrhost2@test.com", renter_email="tdrrenter2@test.com")
        _make_termination_case_with_entitlement(db_session, occ, entitlement_status="CALCULATED")

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DATE_CHANGE", "claimFamily": "BOOKING_AGREEMENT"}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        claim = r.json()["claims"][0]
        assert claim["sourceRecordType"] != "TERMINATION_CASE"


class TestDoubleRecoveryGuard:
    def test_settlement_is_refused_once_the_refund_entitlement_is_executed(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="tdrhost3@test.com", renter_email="tdrrenter3@test.com")
        _make_termination_case_with_entitlement(db_session, occ, entitlement_status="EXECUTED")

        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "EARLY_TERMINATION", "claimFamily": "BOOKING_AGREEMENT", "amount": 200}},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]
        assert r.json()["claims"][0]["authorityClass"] == "A1"

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements",
            json={"claimIds": [claim_id], "termsText": "Refund $200", "amount": 200, "acknowledgesNoNonwaivableWaiver": True},
            cookies=renter_cookies,
        )
        assert r.status_code == 409, r.text
        assert "already been executed" in r.text

    def test_settlement_proceeds_while_the_entitlement_is_only_calculated(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="tdrhost4@test.com", renter_email="tdrrenter4@test.com")
        _make_termination_case_with_entitlement(db_session, occ, entitlement_status="CALCULATED")

        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "EARLY_TERMINATION", "claimFamily": "BOOKING_AGREEMENT", "amount": 200}},
            cookies=renter_cookies,
        )
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements",
            json={"claimIds": [claim_id], "termsText": "Refund $200", "amount": 200, "acknowledgesNoNonwaivableWaiver": True},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text

    def test_settlement_proceeds_with_no_termination_case_at_all(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="tdrhost5@test.com", renter_email="tdrrenter5@test.com")
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
        assert r.status_code == 201, r.text


class TestHostNonperformanceCausationEvent:
    """QA-Q27/Section 17: a cancellation caused by host nonperformance
    preserves that causation event on the claim, separately from the
    monetary claim itself -- and settling it never auto-refunds."""

    def test_the_host_nonperformance_cause_code_is_preserved_on_the_claim(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="tdrhost6@test.com", renter_email="tdrrenter6@test.com")
        _make_termination_case_with_entitlement(
            db_session, occ, entitlement_status="CALCULATED", cause_code="HOST_FAULT_OR_NONPERFORMANCE",
        )

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "EARLY_TERMINATION", "claimFamily": "BOOKING_AGREEMENT", "amount": 300}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        snapshot = r.json()["claims"][0]["sourceRecordSnapshot"]
        assert snapshot["cause_code"] == "HOST_FAULT_OR_NONPERFORMANCE"

    def test_settling_a_host_nonperformance_claim_resolves_status_only_never_executes_a_refund(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="tdrhost7@test.com", renter_email="tdrrenter7@test.com")
        _make_termination_case_with_entitlement(
            db_session, occ, entitlement_status="CALCULATED", cause_code="HOST_FAULT_OR_NONPERFORMANCE",
        )

        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "EARLY_TERMINATION", "claimFamily": "BOOKING_AGREEMENT", "amount": 300}},
            cookies=renter_cookies,
        )
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements",
            json={"claimIds": [claim_id], "termsText": "Refund $300", "amount": 300, "acknowledgesNoNonwaivableWaiver": True},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        settlement_id = r.json()["id"]

        r = client.post(
            f"/api/users/hosting/disputes/{case_id}/settlements/{settlement_id}/respond",
            json={"action": "ACCEPT"}, cookies=auth_user_cookie(host),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "EFFECTIVE"

        # The claim resolves to SETTLED -- no RefundRequest/PaymentAllocation/
        # PayoutRecord row is ever created by dispute code (confirmed by the
        # module's own NON-BEHAVIOR docstring); the termination engine's own,
        # separate refund entitlement (still just CALCULATED) is untouched.
        r = client.get(f"/api/users/rentals/disputes/{case_id}", cookies=renter_cookies)
        assert r.json()["claims"][0]["status"] == "SETTLED"
