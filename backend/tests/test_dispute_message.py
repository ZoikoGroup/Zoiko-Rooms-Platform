"""Integration tests for the ZR-ENG-CLR-010 Section 11/19/23 case-room
messaging: app/crud/dispute_message.py and the /messages routes added to
app/api/routes/disputes.py.

Covers: renter/host can exchange party-visible messages and see each
other's; an admin's INTERNAL_ONLY note is invisible to both parties;
moderating (hiding) a message removes it from party view without
deleting the row; Section 19's safety gate blocks a renter/host from
posting on a PROTECTED_SAFETY case while an admin can still post."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


class TestPartyMessaging:
    def test_renter_and_host_see_each_others_messages(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dmhost1@test.com", renter_email="dmrenter1@test.com")
        renter_cookies, host_cookies = auth_user_cookie(renter), auth_user_cookie(host)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]

        r = client.post(f"/api/users/rentals/disputes/{case_id}/messages", json={"body": "Please review my receipt"}, cookies=renter_cookies)
        assert r.status_code == 201, r.text
        assert r.json()["senderRole"] == "RENTER"

        r = client.post(f"/api/users/hosting/disputes/{case_id}/messages", json={"body": "Reviewing now"}, cookies=host_cookies)
        assert r.status_code == 201, r.text

        r = client.get(f"/api/users/hosting/disputes/{case_id}/messages", cookies=host_cookies)
        assert r.status_code == 200, r.text
        bodies = [m["body"] for m in r.json()]
        assert "Please review my receipt" in bodies
        assert "Reviewing now" in bodies

    def test_admin_internal_note_is_invisible_to_both_parties(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dmhost2@test.com", renter_email="dmrenter2@test.com")
        renter_cookies, host_cookies = auth_user_cookie(renter), auth_user_cookie(host)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]

        admin = _make_admin(db_session, email="dm-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/{case_id}/messages",
            json={"body": "Internal risk flag -- do not share", "visibilityClass": "INTERNAL_ONLY"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        assert r.json()["visibilityClass"] == "INTERNAL_ONLY"
        internal_message_id = r.json()["id"]

        r = client.get(f"/api/users/rentals/disputes/{case_id}/messages", cookies=renter_cookies)
        assert all(m["id"] != internal_message_id for m in r.json())

        r = client.get(f"/api/admin/disputes/{case_id}/messages", cookies=admin_cookies)
        assert any(m["id"] == internal_message_id for m in r.json())


class TestModeration:
    def test_hiding_a_message_removes_it_from_party_view_but_keeps_the_row(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dmhost3@test.com", renter_email="dmrenter3@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]

        r = client.post(f"/api/users/rentals/disputes/{case_id}/messages", json={"body": "inappropriate content"}, cookies=renter_cookies)
        message_id = r.json()["id"]

        admin = _make_admin(db_session, email="dm-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(f"/api/admin/disputes/messages/{message_id}/moderate", json={"hidden": True}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["moderationState"] == "HIDDEN"

        r = client.get(f"/api/users/rentals/disputes/{case_id}/messages", cookies=renter_cookies)
        assert all(m["id"] != message_id for m in r.json())

        r = client.get(f"/api/admin/disputes/{case_id}/messages", cookies=admin_cookies)
        assert any(m["id"] == message_id for m in r.json())


class TestSafetyGate:
    def test_party_to_party_messaging_disabled_on_a_protected_safety_case(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="dmhost4@test.com", renter_email="dmrenter4@test.com")
        renter_cookies, host_cookies = auth_user_cookie(renter), auth_user_cookie(host)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DISCRIMINATION", "claimFamily": "PROTECTED_SAFETY"}},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        case_id = r.json()["id"]

        r = client.post(f"/api/users/rentals/disputes/{case_id}/messages", json={"body": "trying to message the host"}, cookies=renter_cookies)
        assert r.status_code == 403, r.text

        r = client.post(f"/api/users/hosting/disputes/{case_id}/messages", json={"body": "trying to message the renter"}, cookies=host_cookies)
        assert r.status_code == 403, r.text

        admin = _make_admin(db_session, email="dm-admin3@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/{case_id}/messages", json={"body": "Zoiko is reviewing this safety report"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
