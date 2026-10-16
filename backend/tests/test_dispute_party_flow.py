"""Host<->renter dispute claims move on the parties' own actions and
deadlines -- crud/dispute_party_flow.py. No admin decision."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.crud.dispute_party_flow import apply_dispute_deadlines
from app.models.dispute import DisputeResolutionCase, DisputeResolutionClaim
from app.models.dispute_deadline import DisputeDeadline
from tests.conftest import auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties

RENTER = "/api/users/rentals/disputes"
HOST = "/api/users/hosting/disputes"


def _renter_claim(client, db_session: Session, suffix: str, family: str = "PROPERTY_CONDITION", code: str = "REPAIRS"):
    host, renter, occupancy = _make_occupancy_with_parties(db_session, host_email=f"dpf-host-{suffix}@test.com", renter_email=f"dpf-renter-{suffix}@test.com")
    r = client.post(RENTER, json={"occupancyId": occupancy.id, "claim": {
        "claimCode": code, "claimFamily": family, "amount": 300, "requestedRemedy": "Fix it"}}, cookies=auth_user_cookie(renter))
    assert r.status_code == 201, r.text
    case = r.json()
    return host, renter, case["id"], case["claims"][0]["id"]


class TestRespondent:
    def test_host_accepts_and_the_case_closes(self, client, db_session: Session):
        host, _renter, case_id, claim_id = _renter_claim(client, db_session, "a")
        r = client.post(f"{HOST}/{case_id}/claims/{claim_id}/respond", json={"response": "ACCEPT"}, cookies=auth_user_cookie(host))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "UPHELD"
        db_session.expire_all()
        assert db_session.get(DisputeResolutionCase, case_id).status == "CLOSED"

    def test_claimant_cannot_answer_their_own_claim(self, client, db_session: Session):
        _host, renter, case_id, claim_id = _renter_claim(client, db_session, "b")
        r = client.post(f"{RENTER}/{case_id}/claims/{claim_id}/respond", json={"response": "ACCEPT"}, cookies=auth_user_cookie(renter))
        assert r.status_code == 403

    def test_contest_opens_a_negotiation_window(self, client, db_session: Session):
        host, _renter, case_id, claim_id = _renter_claim(client, db_session, "c")
        r = client.post(f"{HOST}/{case_id}/claims/{claim_id}/respond", json={"response": "CONTEST", "note": "Not my fault"}, cookies=auth_user_cookie(host))
        assert r.json()["status"] == "NEGOTIATION"
        window = db_session.query(DisputeDeadline).filter_by(claim_id=claim_id, deadline_type="EVIDENCE_CLOSE").one()
        assert window.status == "PENDING"


class TestDeadlines:
    def test_no_answer_by_the_deadline_accepts_the_claim(self, client, db_session: Session):
        _host, _renter, case_id, claim_id = _renter_claim(client, db_session, "d")
        d = db_session.query(DisputeDeadline).filter_by(claim_id=claim_id, deadline_type="PARTY_RESPONSE").one()
        d.due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()
        assert apply_dispute_deadlines(db_session) == 1
        db_session.expire_all()
        assert db_session.get(DisputeResolutionClaim, claim_id).status == "UPHELD"
        assert apply_dispute_deadlines(db_session) == 0  # idempotent

    def test_no_settlement_goes_to_the_outside_body_then_a_party_records_the_decision(self, client, db_session: Session):
        host, renter, case_id, claim_id = _renter_claim(client, db_session, "e")
        client.post(f"{HOST}/{case_id}/claims/{claim_id}/respond", json={"response": "CONTEST"}, cookies=auth_user_cookie(host))
        window = db_session.query(DisputeDeadline).filter_by(claim_id=claim_id, deadline_type="EVIDENCE_CLOSE").one()
        window.due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()
        apply_dispute_deadlines(db_session)
        db_session.expire_all()
        assert db_session.get(DisputeResolutionClaim, claim_id).status == "EXTERNAL_REFERRAL"

        r = client.post(f"{RENTER}/{case_id}/external-proceedings", json={
            "authorityType": "TRIBUNAL_COURT", "authorityName": "Rent tribunal", "externalReference": "RT-2026-77", "claimIds": [claim_id]},
            cookies=auth_user_cookie(renter))
        assert r.status_code == 201, r.text
        proceeding_id = r.json()["id"]
        r = client.post(f"{HOST}/{case_id}/external-proceedings/{proceeding_id}/decision", json={
            "outcome": "PARTLY_UPHELD", "decisionDate": date.today().isoformat(), "finalityState": "FINAL"},
            cookies=auth_user_cookie(host))
        assert r.status_code == 200, r.text
        db_session.expire_all()
        assert db_session.get(DisputeResolutionClaim, claim_id).status == "PARTLY_UPHELD"
        assert db_session.get(DisputeResolutionCase, case_id).status == "CLOSED"


class TestZoikoHandledClaimsStayWithZoiko:
    def test_a_safety_claim_cannot_be_settled_by_the_parties(self, client, db_session: Session):
        host, _renter, case_id, claim_id = _renter_claim(client, db_session, "f", family="PROTECTED_SAFETY", code="DISCRIMINATION")
        r = client.post(f"{HOST}/{case_id}/claims/{claim_id}/respond", json={"response": "ACCEPT"}, cookies=auth_user_cookie(host))
        assert r.status_code == 409
