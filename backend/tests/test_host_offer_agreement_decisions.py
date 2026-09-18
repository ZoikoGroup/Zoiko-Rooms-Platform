"""ZR-ENG-CLR-004 Section 4.3: "The default is the legal landlord/Host...
Zoiko Admin does not sign merely because Zoiko operates the platform." Before
this, the entire offer/agreement pipeline (create offer, set terms, send,
generate agreement, send, sign as provider) was admin-portal only -- a
self-service Host (UserAccount, no admin_users/Membership row) had no route
into any of it. Covers /api/users/hosting/{applications/{id}/offers,
offers/{id}/terms, offers/{id}/send, offers/{id}/agreement,
agreements/{id}/send, agreements/{id}/sign} end to end.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.authority_record import AuthorityRecord
from app.models.leasing import ApplicationDecision
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.notification import Notification
from app.models.occupancy_classification import OccupancyClassification
from app.models.room import Room
from tests.conftest import auth_user_cookie
from tests.test_host_application_decisions import _make_second_host_and_listing
from tests.test_self_listing_restriction import _make_host_and_listing, _make_verified_renter


def _make_agreement_eligible(db: Session, listing_id: str) -> None:
    """Same eligibility setup as test_renter_offer_agreement_flow.py's own
    helper -- agreement creation requires an active market release, a
    verified authority record for the room, and a resolved occupancy
    classification."""
    listing = db.get(Listing, listing_id)
    room = db.get(Room, listing.room_id)

    release = db.query(MarketRelease).filter(MarketRelease.jurisdiction == "England", MarketRelease.status == "active").first()
    if release is None:
        release = MarketRelease(jurisdiction="England", status="active")
        db.add(release)
        db.flush()
    listing.market_release_id = release.id

    db.add(
        AuthorityRecord(
            party_id=listing.party_id, room_id=room.id, authority_type="lease_agreement", status="verified",
        )
    )
    db.add(OccupancyClassification(room_id=room.id, classification="shared_residential_room", review_state="APPROVED"))
    db.commit()


def _host_deliver_all_disclosures(client, host_cookies: dict, agreement_id: int) -> None:
    """Same as conftest.deliver_all_disclosures, but through the Host's own
    /api/users/hosting/agreements/... routes instead of the admin-portal
    /api/leasing/... ones."""
    r = client.get(f"/api/users/hosting/agreements/{agreement_id}/disclosures", cookies=host_cookies)
    assert r.status_code == 200, r.text
    for disclosure in r.json():
        r = client.post(
            f"/api/users/hosting/agreements/{agreement_id}/disclosures/{disclosure['id']}/deliver",
            cookies=host_cookies,
        )
        assert r.status_code == 200, r.text


def _submit_and_approve_application(client, host, renter, listing_id: str) -> int:
    r = client.post(
        "/api/users/rentals/applications",
        json={"listingId": listing_id, "message": "Interested"},
        cookies=auth_user_cookie(renter),
    )
    assert r.status_code == 201, r.text
    application_id = r.json()["id"]

    r = client.post(
        f"/api/users/hosting/applications/{application_id}/decide",
        json={"decision": "APPROVED"},
        cookies=auth_user_cookie(host),
    )
    assert r.status_code == 200, r.text
    return application_id


class TestHostOfferAgreementFlow:
    def test_host_can_run_the_whole_offer_and_agreement_pipeline_and_sign_as_provider(
        self, client, db_session: Session
    ):
        host, listing_id = _make_host_and_listing(db_session, email="offer-host@test.com")
        renter = _make_verified_renter(db_session, email="offer-renter@test.com")
        host_cookies = auth_user_cookie(host)
        renter_cookies = auth_user_cookie(renter)

        application_id = _submit_and_approve_application(client, host, renter, listing_id)

        r = client.post(f"/api/users/hosting/applications/{application_id}/offers", cookies=host_cookies)
        assert r.status_code == 201, r.text
        offer_id = r.json()["id"]

        r = client.post(
            f"/api/users/hosting/offers/{offer_id}/terms",
            json={
                "monthlyRent": 500, "depositAmount": 500,
                "startDate": (date.today() + timedelta(days=5)).isoformat(), "termMonths": 6,
            },
            cookies=host_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(f"/api/users/hosting/offers/{offer_id}/send", cookies=host_cookies)
        assert r.status_code == 200, r.text

        offer_notification = db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == renter.id, Notification.notification_type == "offer.sent",
            )
        )
        assert offer_notification is not None

        r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ACCEPTED"

        _make_agreement_eligible(db_session, listing_id)
        r = client.post(f"/api/users/hosting/offers/{offer_id}/agreement", cookies=host_cookies)
        assert r.status_code == 201, r.text
        agreement_id = r.json()["id"]

        r = client.post(f"/api/users/hosting/agreements/{agreement_id}/send", cookies=host_cookies)
        assert r.status_code == 200, r.text

        _host_deliver_all_disclosures(client, host_cookies, agreement_id)

        r = client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PARTIALLY_EXECUTED"

        r = client.post(f"/api/users/hosting/agreements/{agreement_id}/sign", cookies=host_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["signedByProviderAt"] is not None
        # Both signatures alone open a checkout window rather than confirming
        # the agreement outright -- same ZR-ENG-CLR-001 Rule 7 as the admin/
        # renter-signed path.
        assert r.json()["status"] == "PAYMENT_IN_PROGRESS"

    def test_a_different_host_cannot_create_an_offer_for_someone_elses_application(
        self, client, db_session: Session
    ):
        host, listing_id = _make_host_and_listing(db_session, email="offer-host-a@test.com")
        other_host, _ = _make_second_host_and_listing(
            db_session, email="offer-host-b@test.com", listing_id="L-SELFTEST5"
        )
        renter = _make_verified_renter(db_session, email="offer-renter-b@test.com")

        application_id = _submit_and_approve_application(client, host, renter, listing_id)

        r = client.post(
            f"/api/users/hosting/applications/{application_id}/offers", cookies=auth_user_cookie(other_host)
        )
        assert r.status_code == 403, r.text

    def test_unauthenticated_request_is_rejected(self, client):
        r = client.get("/api/users/hosting/offers/1")
        assert r.status_code == 401, r.text
