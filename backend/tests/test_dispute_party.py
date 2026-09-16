"""Integration tests for the ZR-ENG-CLR-010 Section 23/25 dispute_party
entity: crud/dispute_party.py, the auto-created SELF rows in
crud/disputes.py:open_case, and the per-party messaging restriction
crud/dispute_message.py:assert_messaging_allowed now checks.

Covers: opening a case on an occupancy auto-creates two verified SELF
rows (renter + host); admin adds a representative for the host side with
a recorded authority basis (QA Q39); a party with a communication
restriction set is refused when posting a case message while the
unrestricted counterparty still can; the existing blanket safety-case
messaging block (Phase 11) is unaffected when no per-party restriction is
set."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


def _open_deposit_case(client, renter_cookies, occupancy_id: int) -> int:
    r = client.post(
        "/api/users/rentals/disputes",
        json={"occupancyId": occupancy_id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestAutoCreatedSelfParties:
    def test_opening_a_case_creates_two_verified_self_rows(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dphost1@test.com", renter_email="dprenter1@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id = _open_deposit_case(client, renter_cookies, occ.id)

        r = client.get(f"/api/users/rentals/disputes/{case_id}/parties", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        parties = r.json()
        assert len(parties) == 2
        roles = {p["partyRole"] for p in parties}
        assert roles == {"RENTER", "HOST"}
        for p in parties:
            assert p["representationType"] == "SELF"
            assert p["authorityVerifiedAt"] is not None
            assert p["addedByAdminId"] is None


class TestRepresentatives:
    def test_admin_adds_a_representative_with_recorded_authority(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dphost2@test.com", renter_email="dprenter2@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id = _open_deposit_case(client, renter_cookies, occ.id)

        admin = _make_admin(db_session, email="dp-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/{case_id}/parties/representatives",
            json={
                "represents": "HOST", "representationType": "PROPERTY_MANAGER",
                "authorityEvidenceRef": "Signed management agreement, doc #123", "partyId": host.party_id,
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["partyRole"] == "REPRESENTATIVE"
        assert body["represents"] == "HOST"
        assert body["representationType"] == "PROPERTY_MANAGER"
        assert body["authorityEvidenceRef"] == "Signed management agreement, doc #123"
        assert body["authorityVerifiedAt"] is not None
        assert body["addedByAdminId"] == admin.id

        r = client.get(f"/api/admin/disputes/{case_id}/parties", cookies=admin_cookies)
        assert len(r.json()) == 3

    def test_representative_requires_an_authority_evidence_ref(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dphost3@test.com", renter_email="dprenter3@test.com")
        case_id = _open_deposit_case(client, auth_user_cookie(renter), occ.id)

        admin = _make_admin(db_session, email="dp-admin2@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/{case_id}/parties/representatives",
            json={"represents": "HOST", "representationType": "LEGAL_COUNSEL", "authorityEvidenceRef": "", "partyId": host.party_id},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text


class TestPerPartyCommunicationRestriction:
    def test_a_restricted_party_cannot_message_while_the_counterparty_still_can(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dphost4@test.com", renter_email="dprenter4@test.com")
        renter_cookies, host_cookies = auth_user_cookie(renter), auth_user_cookie(host)
        case_id = _open_deposit_case(client, renter_cookies, occ.id)

        admin = _make_admin(db_session, email="dp-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.get(f"/api/admin/disputes/{case_id}/parties", cookies=admin_cookies)
        renter_party_id = next(p["id"] for p in r.json() if p["partyRole"] == "RENTER")

        r = client.post(
            f"/api/admin/disputes/parties/{renter_party_id}/communication-restrictions",
            json={"restrictions": "Renter made threatening remarks -- route through Zoiko only"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert "threatening" in r.json()["communicationRestrictions"]

        r = client.post(f"/api/users/rentals/disputes/{case_id}/messages", json={"body": "hello"}, cookies=renter_cookies)
        assert r.status_code == 403, r.text

        r = client.post(f"/api/users/hosting/disputes/{case_id}/messages", json={"body": "hello back"}, cookies=host_cookies)
        assert r.status_code == 201, r.text

    def test_blanket_safety_block_still_works_with_no_per_party_restriction(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dphost5@test.com", renter_email="dprenter5@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DISCRIMINATION", "claimFamily": "PROTECTED_SAFETY"}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]

        r = client.post(f"/api/users/rentals/disputes/{case_id}/messages", json={"body": "hello"}, cookies=renter_cookies)
        assert r.status_code == 403, r.text
