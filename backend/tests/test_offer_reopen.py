"""ZR-36: after the renter declines an offer the host can make a new one --
the offer reopens as a draft, takes new terms and is sent again."""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.notification import Notification
from tests.conftest import auth_user_cookie
from tests.test_host_offer_agreement_decisions import _submit_and_approve_application
from tests.test_self_listing_restriction import _make_host_and_listing, _make_verified_renter


def _terms(rent: int) -> dict:
    return {"monthlyRent": rent, "depositAmount": rent, "startDate": (date.today() + timedelta(days=5)).isoformat(), "termMonths": 6}


def _sent_offer(client, db_session: Session, suffix: str):
    host, listing_id = _make_host_and_listing(db_session, email=f"reopen-host-{suffix}@test.com")
    renter = _make_verified_renter(db_session, email=f"reopen-renter-{suffix}@test.com")
    host_cookies, renter_cookies = auth_user_cookie(host), auth_user_cookie(renter)
    application_id = _submit_and_approve_application(client, host, renter, listing_id)
    offer_id = client.post(f"/api/users/hosting/applications/{application_id}/offers", cookies=host_cookies).json()["id"]
    assert client.post(f"/api/users/hosting/offers/{offer_id}/terms", json=_terms(500), cookies=host_cookies).status_code == 200
    assert client.post(f"/api/users/hosting/offers/{offer_id}/send", cookies=host_cookies).status_code == 200
    return host, renter, host_cookies, renter_cookies, application_id, offer_id


class TestReopenDeclinedOffer:
    def test_host_makes_a_new_offer_after_the_renter_declines(self, client, db_session: Session):
        host, renter, host_cookies, renter_cookies, application_id, offer_id = _sent_offer(client, db_session, "a")

        r = client.post(f"/api/users/rentals/offers/{offer_id}/decline", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        assert db_session.query(Notification).filter(
            Notification.recipient_user_id == host.id, Notification.notification_type == "offer.declined_for_host",
        ).count() == 1

        r = client.post(f"/api/users/hosting/offers/{offer_id}/reopen", cookies=host_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "DRAFT"

        assert client.post(f"/api/users/hosting/offers/{offer_id}/terms", json=_terms(450), cookies=host_cookies).status_code == 200
        r = client.post(f"/api/users/hosting/offers/{offer_id}/send", cookies=host_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SENT"

        # The renter sees a live offer again, with the new terms, and can accept it.
        [app] = [a for a in client.get("/api/users/rentals/applications", cookies=renter_cookies).json() if a["id"] == application_id]
        assert app["offerStatus"] == "SENT"
        r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ACCEPTED"
        assert float(r.json()["terms"][-1]["monthlyRent"]) == 450

    def test_only_a_closed_offer_can_be_reopened(self, client, db_session: Session):
        _host, _renter, host_cookies, _rc, _app_id, offer_id = _sent_offer(client, db_session, "b")
        r = client.post(f"/api/users/hosting/offers/{offer_id}/reopen", cookies=host_cookies)
        assert r.status_code == 409, r.text

    def test_a_rejected_application_cannot_get_a_new_offer(self, client, db_session: Session):
        _host, _renter, host_cookies, renter_cookies, application_id, offer_id = _sent_offer(client, db_session, "c")
        client.post(f"/api/users/rentals/offers/{offer_id}/decline", cookies=renter_cookies)
        client.post(
            f"/api/users/hosting/applications/{application_id}/decide", json={"decision": "REJECTED"}, cookies=host_cookies,
        )
        r = client.post(f"/api/users/hosting/offers/{offer_id}/reopen", cookies=host_cookies)
        assert r.status_code == 409, r.text


class TestRenterCanApplyAgain:
    def test_renter_who_declined_an_offer_can_apply_again(self, client, db_session: Session):
        """ZR-37: declining an offer closes that application; applying again
        for the same room works and shows as a separate, open application."""
        _host, _renter, _hc, renter_cookies, application_id, offer_id = _sent_offer(client, db_session, "apply-again")
        assert client.post(f"/api/users/rentals/offers/{offer_id}/decline", cookies=renter_cookies).status_code == 200
        listing_id = next(
            a["listingId"] for a in client.get("/api/users/rentals/applications", cookies=renter_cookies).json()
            if a["id"] == application_id
        )
        r = client.post(
            "/api/users/rentals/applications", json={"listingId": listing_id, "message": "Applying again"}, cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        apps = {a["id"]: a for a in client.get("/api/users/rentals/applications", cookies=renter_cookies).json()}
        assert apps[application_id]["offerStatus"] == "DECLINED"
        assert apps[r.json()["id"]]["status"] == "SUBMITTED"
