"""ZR-15: when the host declines an application, the renter's own
applications list says so (with the host's message) instead of a generic
"no longer active" -- status alone is "DECIDED" for approve and reject."""

from sqlalchemy.orm import Session

from app.models.notification import Notification
from tests.conftest import auth_user_cookie
from tests.test_host_offer_agreement_decisions import _submit_and_approve_application
from tests.test_self_listing_restriction import _make_host_and_listing, _make_verified_renter


def _renter_application(client, renter, application_id: int) -> dict:
    r = client.get("/api/users/rentals/applications", cookies=auth_user_cookie(renter))
    assert r.status_code == 200, r.text
    return next(a for a in r.json() if a["id"] == application_id)


class TestRenterSeesHostDecision:
    def test_rejection_and_note_reach_the_renter(self, client, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="rej-host@test.com")
        renter = _make_verified_renter(db_session, email="rej-renter@test.com")
        application_id = _submit_and_approve_application(client, host, renter, listing_id)

        # Shortlisted/approved first.
        app = _renter_application(client, renter, application_id)
        assert app["decision"] == "APPROVED"

        r = client.post(
            f"/api/users/hosting/applications/{application_id}/decide",
            json={"decision": "REJECTED", "note": "Another applicant progressed"},
            cookies=auth_user_cookie(host),
        )
        assert r.status_code == 200, r.text

        app = _renter_application(client, renter, application_id)
        assert app["decision"] == "REJECTED"
        assert app["decisionNote"] == "Another applicant progressed"
        assert app["decidedAt"]
        assert db_session.query(Notification).filter(
            Notification.recipient_user_id == renter.id, Notification.notification_type == "application.rejected",
        ).count() == 1

    def test_undecided_application_has_no_decision(self, client, db_session: Session):
        _host, listing_id = _make_host_and_listing(db_session, email="rej-host2@test.com")
        renter = _make_verified_renter(db_session, email="rej-renter2@test.com")
        r = client.post(
            "/api/users/rentals/applications", json={"listingId": listing_id, "message": "Hi"}, cookies=auth_user_cookie(renter),
        )
        app = _renter_application(client, renter, r.json()["id"])
        assert app["decision"] is None and app["decisionNote"] == ""


class TestHostSeesDecisionsInOrder:
    def test_decisions_come_back_oldest_first(self, client, db_session: Session):
        """ZR-38: the host's list derives each application's state from its
        latest decision, so the order must be stable across page loads."""
        host, listing_id = _make_host_and_listing(db_session, email="order-host@test.com")
        renter = _make_verified_renter(db_session, email="order-renter@test.com")
        r = client.post(
            "/api/users/rentals/applications", json={"listingId": listing_id, "message": "Hi"}, cookies=auth_user_cookie(renter),
        )
        application_id = r.json()["id"]
        for payload in ({"decision": "APPROVED", "reasonCode": "SHORTLISTED"}, {"decision": "APPROVED"}):
            assert client.post(
                f"/api/users/hosting/applications/{application_id}/decide", json=payload, cookies=auth_user_cookie(host),
            ).status_code == 200
        db_session.expire_all()  # each real request has a fresh session; the test shares one
        apps = client.get("/api/users/hosting/applications", cookies=auth_user_cookie(host)).json()
        [app] = [a for a in apps if a["id"] == application_id]
        ids = [d["id"] for d in app["decisions"]]
        assert ids == sorted(ids)
        assert [d["reasonCode"] for d in app["decisions"]] == ["SHORTLISTED", ""]
        # Shortlist then select: the renter is told they were approved once.
        assert db_session.query(Notification).filter(
            Notification.recipient_user_id == renter.id, Notification.notification_type == "application.approved",
        ).count() == 1
