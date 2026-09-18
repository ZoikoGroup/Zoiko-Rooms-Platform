"""ZR-ENG-CLR-011 Section 10/AC-06: a self-service Host can now list and decide
applications on their own party-owned listing directly (previously only a
super_admin could decide any application, and a self-service Host had no route
to even see one). Covers /api/users/hosting/applications end to end.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.guest import Guest
from app.models.identity_verification import IdentityVerification
from app.models.leasing import Application, ApplicationDecision
from app.models.listing import Listing
from app.models.notification import Notification
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.models.user_account import UserAccount
from tests.conftest import _make_user, auth_user_cookie
from tests.test_self_listing_restriction import _make_host_and_listing, _make_verified_renter


def _make_second_host_and_listing(db: Session, *, email: str, listing_id: str) -> tuple[UserAccount, str]:
    """_make_host_and_listing hardcodes id="L-SELFTEST1"/slug="selftest1", so it
    can only be called once per test -- this is that same shape with a caller-
    supplied id/slug, for tests that need two distinct hosts/listings."""
    provider_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(provider_party)
    db.flush()

    host = _make_user(db, email=email)
    host.party_id = provider_party.id
    db.flush()

    db.add(
        IdentityVerification(
            party_id=provider_party.id, document_type="passport", document_category="identity", status="verified",
        )
    )

    prop = Property(owner_party_id=provider_party.id, address="2 Host St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()

    room = Room(property_id=prop.id, room_type="private_room", size=120, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    listing = Listing(
        id=listing_id, slug=listing_id.lower(), name="Another Host's Room", room_type="Private room",
        city="Bengaluru", location="Indiranagar", price_per_night=700, guests=1, rating=4.5, review_count=0,
        party_id=provider_party.id, owner_id=None, room_id=room.id, state="PUBLISHED",
    )
    db.add(listing)
    db.commit()
    return host, listing.id


def _submit_application(client, listing_id: str, renter) -> int:
    r = client.post(
        "/api/users/rentals/applications",
        json={"listingId": listing_id, "message": "Interested in this room"},
        cookies=auth_user_cookie(renter),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestListAndGetHostedApplications:
    def test_host_sees_only_applications_on_their_own_listing(self, client, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="apps-host-a@test.com")
        other_host, other_listing_id = _make_second_host_and_listing(
            db_session, email="apps-host-b@test.com", listing_id="L-SELFTEST2"
        )
        renter = _make_verified_renter(db_session, email="apps-renter-a@test.com")
        other_renter = _make_verified_renter(db_session, email="apps-renter-b@test.com")

        _submit_application(client, listing_id, renter)
        _submit_application(client, other_listing_id, other_renter)

        r = client.get("/api/users/hosting/applications", cookies=auth_user_cookie(host))
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body) == 1
        assert body[0]["listingId"] == listing_id

    def test_get_by_id_403s_for_a_different_hosts_application(self, client, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="apps-host-c@test.com")
        other_host, _ = _make_second_host_and_listing(
            db_session, email="apps-host-d@test.com", listing_id="L-SELFTEST3"
        )
        renter = _make_verified_renter(db_session, email="apps-renter-c@test.com")

        application_id = _submit_application(client, listing_id, renter)

        r = client.get(f"/api/users/hosting/applications/{application_id}", cookies=auth_user_cookie(other_host))
        assert r.status_code == 403, r.text

    def test_get_by_id_returns_own_application(self, client, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="apps-host-e@test.com")
        renter = _make_verified_renter(db_session, email="apps-renter-e@test.com")

        application_id = _submit_application(client, listing_id, renter)

        r = client.get(f"/api/users/hosting/applications/{application_id}", cookies=auth_user_cookie(host))
        assert r.status_code == 200, r.text
        assert r.json()["id"] == application_id

    def test_unauthenticated_request_is_rejected(self, client):
        r = client.get("/api/users/hosting/applications")
        assert r.status_code == 401, r.text


class TestDecideHostedApplication:
    def test_host_can_approve_their_own_application(self, client, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="apps-host-f@test.com")
        renter = _make_verified_renter(db_session, email="apps-renter-f@test.com")
        application_id = _submit_application(client, listing_id, renter)

        r = client.post(
            f"/api/users/hosting/applications/{application_id}/decide",
            json={"decision": "APPROVED", "note": "Great fit"},
            cookies=auth_user_cookie(host),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "DECIDED"

        decision = db_session.query(ApplicationDecision).filter(
            ApplicationDecision.application_id == application_id
        ).one()
        assert decision.decision == "APPROVED"
        assert decision.decided_by_user_id == host.id
        assert decision.decided_by_admin_id is None

        renter_notice = db_session.query(Notification).filter(
            Notification.notification_type == "application.approved",
            Notification.recipient_user_id == renter.id,
        ).one_or_none()
        assert renter_notice is not None

    def test_host_can_reject_their_own_application(self, client, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="apps-host-g@test.com")
        renter = _make_verified_renter(db_session, email="apps-renter-g@test.com")
        application_id = _submit_application(client, listing_id, renter)

        r = client.post(
            f"/api/users/hosting/applications/{application_id}/decide",
            json={"decision": "REJECTED"},
            cookies=auth_user_cookie(host),
        )
        assert r.status_code == 200, r.text

        application = db_session.get(Application, application_id)
        assert application.status == "DECIDED"

    def test_a_different_host_cannot_decide_someone_elses_application(self, client, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="apps-host-h@test.com")
        other_host, _ = _make_second_host_and_listing(
            db_session, email="apps-host-i@test.com", listing_id="L-SELFTEST4"
        )
        renter = _make_verified_renter(db_session, email="apps-renter-h@test.com")
        application_id = _submit_application(client, listing_id, renter)

        r = client.post(
            f"/api/users/hosting/applications/{application_id}/decide",
            json={"decision": "APPROVED"},
            cookies=auth_user_cookie(other_host),
        )
        assert r.status_code == 403, r.text

    def test_cannot_decide_an_already_decided_application(self, client, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="apps-host-j@test.com")
        renter = _make_verified_renter(db_session, email="apps-renter-j@test.com")
        application_id = _submit_application(client, listing_id, renter)

        first = client.post(
            f"/api/users/hosting/applications/{application_id}/decide",
            json={"decision": "APPROVED"},
            cookies=auth_user_cookie(host),
        )
        assert first.status_code == 200, first.text

        second = client.post(
            f"/api/users/hosting/applications/{application_id}/decide",
            json={"decision": "REJECTED"},
            cookies=auth_user_cookie(host),
        )
        assert second.status_code == 409, second.text

    def test_invalid_decision_value_is_rejected(self, client, db_session: Session):
        host, listing_id = _make_host_and_listing(db_session, email="apps-host-k@test.com")
        renter = _make_verified_renter(db_session, email="apps-renter-k@test.com")
        application_id = _submit_application(client, listing_id, renter)

        r = client.post(
            f"/api/users/hosting/applications/{application_id}/decide",
            json={"decision": "MAYBE"},
            cookies=auth_user_cookie(host),
        )
        assert r.status_code == 400, r.text
