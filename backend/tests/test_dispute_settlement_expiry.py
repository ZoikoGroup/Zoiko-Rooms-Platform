"""Integration tests for ZR-ENG-CLR-010 Section 22 settlement auto-expiry:
crud/dispute_settlement.py's new _expire_if_overdue, wired into
get_settlement_or_404/list_settlements_for_case.

Covers: a SENT settlement past its expires_at is lazily flipped to EXPIRED
on the next fetch (no scheduler -- same idiom as
services/booking_expiry.py); respond_settlement/void_settlement both
refuse (409) on an already-expired settlement; a settlement with no
expires_at never auto-expires."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from tests.conftest import auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


def _propose_settlement(client, renter_cookies, case_id: int, claim_id: int, *, expires_at: str | None = None) -> int:
    payload = {"claimIds": [claim_id], "termsText": "Refund $300", "amount": 300, "acknowledgesNoNonwaivableWaiver": True}
    if expires_at is not None:
        payload["expiresAt"] = expires_at
    r = client.post(f"/api/users/rentals/disputes/{case_id}/settlements", json=payload, cookies=renter_cookies)
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestAutoExpiry:
    def test_a_settlement_past_its_expiry_flips_to_expired_on_fetch(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="sehost1@test.com", renter_email="serenter1@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]
        settlement_id = _propose_settlement(client, renter_cookies, case_id, claim_id)

        from app.models.dispute_settlement import DisputeSettlement
        settlement = db_session.get(DisputeSettlement, settlement_id)
        settlement.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db_session.commit()

        r = client.get(f"/api/users/rentals/disputes/{case_id}/settlements", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        body = next(s for s in r.json() if s["id"] == settlement_id)
        assert body["status"] == "EXPIRED"

    def test_respond_and_void_both_refuse_on_an_expired_settlement(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="sehost2@test.com", renter_email="serenter2@test.com")
        renter_cookies, host_cookies = auth_user_cookie(renter), auth_user_cookie(host)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]
        settlement_id = _propose_settlement(client, renter_cookies, case_id, claim_id)

        from app.models.dispute_settlement import DisputeSettlement
        settlement = db_session.get(DisputeSettlement, settlement_id)
        settlement.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db_session.commit()

        r = client.post(
            f"/api/users/hosting/disputes/{case_id}/settlements/{settlement_id}/respond",
            json={"action": "ACCEPT"}, cookies=host_cookies,
        )
        assert r.status_code == 409, r.text

        r = client.post(f"/api/users/rentals/disputes/{case_id}/settlements/{settlement_id}/void", cookies=renter_cookies)
        assert r.status_code == 409, r.text

    def test_a_settlement_with_no_expiry_never_auto_expires(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="sehost3@test.com", renter_email="serenter3@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]
        settlement_id = _propose_settlement(client, renter_cookies, case_id, claim_id)

        r = client.get(f"/api/users/rentals/disputes/{case_id}/settlements", cookies=renter_cookies)
        body = next(s for s in r.json() if s["id"] == settlement_id)
        assert body["status"] == "SENT"
        assert body["expiresAt"] is None
