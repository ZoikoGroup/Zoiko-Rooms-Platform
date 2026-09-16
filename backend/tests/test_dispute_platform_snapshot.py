"""Integration tests for the ZR-ENG-CLR-010 Section 21/Section 2 automatic
platform-snapshot evidence: the new _build_platform_snapshot/
_create_platform_snapshot_evidence helpers wired into
crud/disputes.py:open_case.

Covers: opening a case on an occupancy produces exactly one additional
SYSTEM_RECORD/PARTY_VISIBLE evidence item whose note_text (parsed as JSON)
freezes the occupancy/deposit facts at case-open time, visible in both the
renter's evidence list and the case export; opening a case with no
occupancy (a ZOIKO_SERVICE platform-fee claim) creates zero evidence
items, not an error."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from tests.conftest import auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


class TestSnapshotWithOccupancy:
    def test_opening_a_case_creates_a_system_record_snapshot(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="pshost1@test.com", renter_email="psrenter1@test.com")
        renter_cookies = auth_user_cookie(renter)

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]

        r = client.get(f"/api/users/rentals/disputes/{case_id}/evidence", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        items = r.json()
        assert len(items) == 1
        snapshot_item = items[0]
        assert snapshot_item["provenance"] == "SYSTEM_RECORD"
        assert snapshot_item["disclosureClass"] == "PARTY_VISIBLE"
        assert snapshot_item["claimIds"] == [claim_id]

        snapshot = json.loads(snapshot_item["noteText"])
        assert snapshot["occupancy_id"] == occ.id
        assert snapshot["occupancy_status"] == occ.status

    def test_snapshot_is_visible_in_the_case_export(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="pshost2@test.com", renter_email="psrenter2@test.com")
        renter_cookies = auth_user_cookie(renter)

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]

        r = client.get(f"/api/users/rentals/disputes/{case_id}/export", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["evidenceIndex"]) == 1
        assert body["evidenceIndex"][0]["provenance"] == "SYSTEM_RECORD"
        summaries = " ".join(e["summary"] for e in body["chronology"] if e["eventType"] == "evidence.uploaded")
        assert "SYSTEM_RECORD" in summaries


class TestSnapshotWithoutOccupancy:
    def test_a_case_with_no_occupancy_creates_no_evidence(self, client, db_session: Session):
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="pshost3@test.com", renter_email="psrenter3@test.com")
        renter_cookies = auth_user_cookie(renter)

        r = client.post(
            "/api/users/rentals/disputes",
            json={"claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        case_id = r.json()["id"]

        r = client.get(f"/api/users/rentals/disputes/{case_id}/evidence", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        assert r.json() == []
