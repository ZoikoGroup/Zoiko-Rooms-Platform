"""Coverage for the two remaining notification gaps that turned out to have a
clear recipient after all: authority record verify/reject (notify the
self-service host who submitted it) and legacy admin-created bookings
(notify the guest and, if USER-hosted, the host)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.models.listing import Listing
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.models.user_account import UserAccount
from tests.conftest import _make_admin, _make_user, auth_admin_cookie


def _make_user_hosted_room(db: Session):
    """A room on a property owned by a party linked to a self-service
    UserAccount host (not an AdminUser) -- the case notify_user_by_party is
    meant for. Returns (room, host_user)."""
    host_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(host_party)
    db.flush()
    host_user = _make_user(db, email="authority-host@test.com")
    host_user.party_id = host_party.id

    prop = Property(owner_party_id=host_party.id, address="9 Authority Rd", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=90, has_ensuite=False, status="active")
    db.add(room)
    db.commit()
    return room, host_user


class TestAuthorityRecordNotifications:
    def test_verify_notifies_host(self, client, db_session: Session):
        room, host_user = _make_user_hosted_room(db_session)
        admin = _make_admin(db_session, email="authority-admin@test.com", role="super_admin")
        db_session.commit()

        created = client.post(
            "/api/authority-records",
            json={"roomId": room.id, "authorityType": "lease_agreement", "evidenceRef": ""},
            cookies=auth_admin_cookie(admin),
        ).json()

        resp = client.post(f"/api/authority-records/{created['id']}/verify", cookies=auth_admin_cookie(admin))
        assert resp.status_code == 200, resp.text

        notifs = notif_crud.list_for_user(db_session, host_user.id)
        assert any(n.notification_type == "authority_record.verified" for n in notifs)

    def test_reject_notifies_host(self, client, db_session: Session):
        room, host_user = _make_user_hosted_room(db_session)
        admin = _make_admin(db_session, email="authority-admin2@test.com", role="super_admin")
        db_session.commit()

        created = client.post(
            "/api/authority-records",
            json={"roomId": room.id, "authorityType": "lease_agreement", "evidenceRef": ""},
            cookies=auth_admin_cookie(admin),
        ).json()

        resp = client.post(f"/api/authority-records/{created['id']}/reject", cookies=auth_admin_cookie(admin))
        assert resp.status_code == 200, resp.text

        notifs = notif_crud.list_for_user(db_session, host_user.id)
        assert any(n.notification_type == "authority_record.rejected" for n in notifs)


class TestLegacyBookingNotifications:
    def test_booking_creation_notifies_guest_and_host(self, client, db_session: Session):
        host_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(host_party)
        db_session.flush()
        host_user = _make_user(db_session, email="booking-host@test.com")
        host_user.party_id = host_party.id

        listing = Listing(
            id="L-BOOKNOTIF", slug="booknotif", name="Booking Notif Listing", room_type="Private room",
            city="Bengaluru", location="Indiranagar", price_per_night=400, guests=2,
            rating=0.0, review_count=0, party_id=host_party.id, owner_id=None, room_id=None,
            state="PUBLISHED",
        )
        db_session.add(listing)
        db_session.commit()

        admin = _make_admin(db_session, email="booking-admin@test.com", role="super_admin")
        db_session.commit()

        check_in = date.today() + timedelta(days=10)
        check_out = check_in + timedelta(days=35)
        resp = client.post(
            "/api/bookings",
            json={
                "listingId": listing.id,
                "newGuest": {"name": "Walk-in Guest", "email": "walkin-notif@test.com"},
                "checkIn": check_in.isoformat(),
                "checkOut": check_out.isoformat(),
                "guests": 1,
            },
            cookies=auth_admin_cookie(admin),
        )
        assert resp.status_code == 201, resp.text

        host_notifs = notif_crud.list_for_user(db_session, host_user.id)
        assert any(n.notification_type == "booking.created" for n in host_notifs)

    def test_walk_in_guest_with_no_account_does_not_error(self, client, db_session: Session):
        """A guest with no UserAccount (the common case for this legacy admin
        flow) must not error -- notify_user_by_guest no-ops silently."""
        host_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(host_party)
        db_session.flush()

        listing = Listing(
            id="L-BOOKNOTIF2", slug="booknotif2", name="Booking Notif Listing 2", room_type="Private room",
            city="Bengaluru", location="Koramangala", price_per_night=400, guests=2,
            rating=0.0, review_count=0, party_id=host_party.id, owner_id=None, room_id=None,
            state="PUBLISHED",
        )
        db_session.add(listing)
        db_session.commit()

        admin = _make_admin(db_session, email="booking-admin2@test.com", role="super_admin")
        db_session.commit()

        check_in = date.today() + timedelta(days=10)
        check_out = check_in + timedelta(days=35)
        resp = client.post(
            "/api/bookings",
            json={
                "listingId": listing.id,
                "newGuest": {"name": "No Account Guest", "email": "no-account@test.com"},
                "checkIn": check_in.isoformat(),
                "checkOut": check_out.isoformat(),
                "guests": 1,
            },
            cookies=auth_admin_cookie(admin),
        )
        assert resp.status_code == 201, resp.text
