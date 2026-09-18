"""Integration tests for two Section 23 data-model fields:

- DisputeSettlement.accepted_party_snapshot -- frozen at the moment a
  settlement is ACCEPTED (see app/crud/dispute_settlement.py:
  respond_settlement's ACCEPT branch); stays empty before acceptance and on
  REJECT/COUNTER.
- DisputeEvidenceItem.captured_at -- an optional, self-reported "when did
  this actually happen" field distinct from created_at ("when was it
  uploaded"); accepted at upload time on all three upload routes."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from tests.conftest import auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


def _open_deposit_case(client, renter_cookies, occupancy_id: int, *, amount: float = 600) -> tuple[int, int]:
    r = client.post(
        "/api/users/rentals/disputes",
        json={"occupancyId": occupancy_id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": amount}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], body["claims"][0]["id"]


class TestSettlementAcceptedPartySnapshot:
    def test_accepting_a_settlement_freezes_a_snapshot(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="snaphost1@test.com", renter_email="snaprenter1@test.com")
        renter_cookies = auth_user_cookie(renter)
        host_cookies = auth_user_cookie(host)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        propose = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements",
            json={"claimIds": [claim_id], "termsText": "Refund $300", "amount": 300, "currency": "INR", "acknowledgesNoNonwaivableWaiver": True},
            cookies=renter_cookies,
        )
        assert propose.status_code == 201, propose.text
        settlement = propose.json()
        assert settlement["acceptedPartySnapshot"] == {}

        accept = client.post(
            f"/api/users/hosting/disputes/{case_id}/settlements/{settlement['id']}/respond",
            json={"action": "ACCEPT"},
            cookies=host_cookies,
        )
        assert accept.status_code == 200, accept.text
        snapshot = accept.json()["acceptedPartySnapshot"]
        assert snapshot["responder_role"] == "HOST"
        assert snapshot["responder_party_id"] is not None
        assert snapshot["responder_guest_id"] is None
        assert snapshot["terms_hash"] == settlement["termsHash"]
        assert snapshot["amount"] == 300
        assert snapshot["currency"] == "INR"
        assert snapshot["responded_at"] is not None

    def test_rejecting_a_settlement_leaves_the_snapshot_empty(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="snaphost2@test.com", renter_email="snaprenter2@test.com")
        renter_cookies = auth_user_cookie(renter)
        host_cookies = auth_user_cookie(host)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        propose = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements",
            json={"claimIds": [claim_id], "termsText": "Refund $300", "amount": 300, "currency": "INR", "acknowledgesNoNonwaivableWaiver": True},
            cookies=renter_cookies,
        )
        assert propose.status_code == 201, propose.text
        settlement_id = propose.json()["id"]

        reject = client.post(
            f"/api/users/hosting/disputes/{case_id}/settlements/{settlement_id}/respond",
            json={"action": "REJECT"},
            cookies=host_cookies,
        )
        assert reject.status_code == 200, reject.text
        assert reject.json()["acceptedPartySnapshot"] == {}


class TestEvidenceCapturedAt:
    def test_renter_can_record_an_optional_captured_at(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="caphost1@test.com", renter_email="caprenter1@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, _claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        captured_at = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence",
            data={"note_text": "photo of the room", "captured_at": captured_at.isoformat()},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["capturedAt"] is not None
        assert body["capturedAt"].startswith("2026-01-05T12:00:00")

    def test_captured_at_defaults_to_none_when_not_provided(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="caphost2@test.com", renter_email="caprenter2@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, _claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence",
            data={"note_text": "no capture time given"},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        assert r.json()["capturedAt"] is None
