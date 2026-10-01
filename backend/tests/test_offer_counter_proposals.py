"""Renter counter-offers on a SENT offer: next to accept/decline, the renter
can propose the rent/deposit they can pay instead. The host answers it
manually (accept -> new terms version, decline -> terms stand, or send
revised terms), and the renter still accepts the offer themselves.

Covers POST /api/users/rentals/offers/{id}/counter and
/api/users/hosting/offers/{id}/counters/{counter_id}/{accept,reject}.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notification import Notification
from tests.conftest import auth_user_cookie
from tests.test_host_application_decisions import _make_second_host_and_listing
from tests.test_host_offer_agreement_decisions import _submit_and_approve_application
from tests.test_self_listing_restriction import _make_host_and_listing, _make_verified_renter

START = date.today() + timedelta(days=5)


def _sent_offer(client, db: Session, suffix: str):
    host, listing_id = _make_host_and_listing(db, email=f"counter-host-{suffix}@test.com")
    renter = _make_verified_renter(db, email=f"counter-renter-{suffix}@test.com")
    application_id = _submit_and_approve_application(client, host, renter, listing_id)
    host_cookies = auth_user_cookie(host)

    r = client.post(f"/api/users/hosting/applications/{application_id}/offers", cookies=host_cookies)
    assert r.status_code == 201, r.text
    offer_id = r.json()["id"]
    r = client.post(
        f"/api/users/hosting/offers/{offer_id}/terms",
        json={"monthlyRent": 500, "depositAmount": 500, "startDate": START.isoformat(), "termMonths": 6},
        cookies=host_cookies,
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/users/hosting/offers/{offer_id}/send", cookies=host_cookies)
    assert r.status_code == 200, r.text
    return host, renter, offer_id


def _counter(client, renter, offer_id, **overrides):
    payload = {"monthlyRent": 400, "depositAmount": 400, "message": "This is what I can pay"}
    payload.update(overrides)
    return client.post(f"/api/users/rentals/offers/{offer_id}/counter", json=payload, cookies=auth_user_cookie(renter))


def _notification(db: Session, user_id: int, notification_type: str):
    return db.scalar(
        select(Notification).where(
            Notification.recipient_user_id == user_id, Notification.notification_type == notification_type,
        )
    )


class TestRenterCounter:
    def test_counter_keeps_offer_sent_and_notifies_host(self, client, db_session: Session):
        host, renter, offer_id = _sent_offer(client, db_session, "a")

        r = _counter(client, renter, offer_id)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "SENT"
        assert len(body["terms"]) == 1
        [counter] = body["counterProposals"]
        assert counter["status"] == "PENDING"
        assert counter["monthlyRent"] == 400
        assert counter["basedOnTermsVersion"] == 1
        assert counter["startDate"] is None and counter["termMonths"] is None
        assert _notification(db_session, host.id, "offer.countered_for_host") is not None

    def test_only_one_pending_counter_at_a_time(self, client, db_session: Session):
        _, renter, offer_id = _sent_offer(client, db_session, "b")
        assert _counter(client, renter, offer_id).status_code == 201
        r = _counter(client, renter, offer_id, monthlyRent=450)
        assert r.status_code == 409, r.text

    def test_counter_identical_to_current_terms_is_rejected(self, client, db_session: Session):
        _, renter, offer_id = _sent_offer(client, db_session, "c")
        r = _counter(client, renter, offer_id, monthlyRent=500, depositAmount=500)
        assert r.status_code == 400, r.text

    def test_counter_must_respect_the_market_deposit_cap(self, client, db_session: Session):
        _, renter, offer_id = _sent_offer(client, db_session, "d")
        r = _counter(client, renter, offer_id, monthlyRent=100, depositAmount=100000)
        assert r.status_code == 400, r.text
        assert "Deposit amount" in r.text

    def test_another_renter_cannot_counter(self, client, db_session: Session):
        _, _, offer_id = _sent_offer(client, db_session, "e")
        stranger = _make_verified_renter(db_session, email="counter-stranger@test.com")
        r = _counter(client, stranger, offer_id)
        assert r.status_code == 403, r.text


class TestHostAnswersCounter:
    def test_accepting_creates_new_terms_and_renter_still_accepts(self, client, db_session: Session):
        host, renter, offer_id = _sent_offer(client, db_session, "f")
        counter_id = _counter(client, renter, offer_id, termMonths=12).json()["counterProposals"][0]["id"]

        r = client.post(
            f"/api/users/hosting/offers/{offer_id}/counters/{counter_id}/accept",
            json={"note": "Fine by me"}, cookies=auth_user_cookie(host),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "SENT"
        latest = body["terms"][-1]
        assert latest["version"] == 2
        assert latest["monthlyRent"] == 400 and latest["depositAmount"] == 400
        assert latest["termMonths"] == 12
        assert latest["startDate"] == START.isoformat()  # carried over, renter left it unchanged
        assert body["counterProposals"][0]["status"] == "ACCEPTED"
        assert body["counterProposals"][0]["responseNote"] == "Fine by me"
        assert _notification(db_session, renter.id, "offer.counter_accepted") is not None

        r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ACCEPTED"

    def test_declining_keeps_terms_and_renter_can_counter_again(self, client, db_session: Session):
        host, renter, offer_id = _sent_offer(client, db_session, "g")
        counter_id = _counter(client, renter, offer_id).json()["counterProposals"][0]["id"]

        r = client.post(
            f"/api/users/hosting/offers/{offer_id}/counters/{counter_id}/reject", cookies=auth_user_cookie(host),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["terms"]) == 1 and body["terms"][0]["monthlyRent"] == 500
        assert body["counterProposals"][0]["status"] == "REJECTED"
        assert _notification(db_session, renter.id, "offer.counter_rejected") is not None

        assert _counter(client, renter, offer_id, monthlyRent=450, depositAmount=450).status_code == 201

    def test_a_counter_can_only_be_answered_once(self, client, db_session: Session):
        host, renter, offer_id = _sent_offer(client, db_session, "h")
        counter_id = _counter(client, renter, offer_id).json()["counterProposals"][0]["id"]
        host_cookies = auth_user_cookie(host)
        assert client.post(f"/api/users/hosting/offers/{offer_id}/counters/{counter_id}/reject", cookies=host_cookies).status_code == 200
        r = client.post(f"/api/users/hosting/offers/{offer_id}/counters/{counter_id}/accept", cookies=host_cookies)
        assert r.status_code == 409, r.text

    def test_another_host_cannot_answer(self, client, db_session: Session):
        _, renter, offer_id = _sent_offer(client, db_session, "i")
        counter_id = _counter(client, renter, offer_id).json()["counterProposals"][0]["id"]
        other_host, _ = _make_second_host_and_listing(
            db_session, email="counter-other-host@test.com", listing_id="L-COUNTEROTHER",
        )
        r = client.post(
            f"/api/users/hosting/offers/{offer_id}/counters/{counter_id}/accept", cookies=auth_user_cookie(other_host),
        )
        assert r.status_code in (403, 404), r.text

    def test_revised_terms_supersede_a_pending_counter_and_notify_renter(self, client, db_session: Session):
        host, renter, offer_id = _sent_offer(client, db_session, "j")
        _counter(client, renter, offer_id)

        r = client.post(
            f"/api/users/hosting/offers/{offer_id}/terms",
            json={"monthlyRent": 450, "depositAmount": 450, "startDate": START.isoformat(), "termMonths": 6},
            cookies=auth_user_cookie(host),
        )
        assert r.status_code == 200, r.text
        offer = client.get(f"/api/users/hosting/offers/{offer_id}", cookies=auth_user_cookie(host)).json()
        assert offer["status"] == "SENT"
        assert offer["counterProposals"][0]["status"] == "SUPERSEDED"
        assert _notification(db_session, renter.id, "offer.terms_revised") is not None


class TestRenterActsWhileCounterPending:
    def test_accepting_the_original_offer_supersedes_the_counter(self, client, db_session: Session):
        _, renter, offer_id = _sent_offer(client, db_session, "k")
        _counter(client, renter, offer_id)

        r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ACCEPTED"
        assert body["terms"][-1]["monthlyRent"] == 500
        assert body["counterProposals"][0]["status"] == "SUPERSEDED"

    def test_declining_supersedes_the_counter(self, client, db_session: Session):
        _, renter, offer_id = _sent_offer(client, db_session, "l")
        _counter(client, renter, offer_id)

        r = client.post(f"/api/users/rentals/offers/{offer_id}/decline", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.json()["counterProposals"][0]["status"] == "SUPERSEDED"
