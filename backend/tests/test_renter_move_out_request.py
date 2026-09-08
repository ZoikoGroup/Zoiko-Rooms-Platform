"""Coverage for the renter move-out request gap found during a full
customer-journey audit: previously there was no way for a renter to signal
intent to vacate at all -- end_occupancy was 100% admin-only with no renter
input into scheduling it."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.models.guest import Guest
from app.models.leasing import Agreement, Application, Offer
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin, _make_user, auth_user_cookie


def _make_active_occupancy(db: Session):
    """A USER-hosted, ACTIVE occupancy. Returns (occupancy, renter_user, host_user)."""
    host_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(host_party)
    db.flush()
    host_user = _make_user(db, email="vacate-host@test.com")
    host_user.party_id = host_party.id

    prop = Property(owner_party_id=host_party.id, address="1 Vacate St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    renter_user = _make_user(db, email="vacate-renter@test.com")
    guest = Guest(id="G-VACATE", name="Vacate Renter", email="vacate-renter@test.com", joined_at=date.today(), user_account_id=renter_user.id)
    db.add(guest)
    db.flush()

    listing = Listing(
        id="L-VACATETEST", slug="vacatetest", name="Vacate Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=0.0, review_count=0, party_id=host_party.id, owner_id=None, room_id=room.id,
        state="PUBLISHED",
    )
    db.add(listing)
    db.flush()

    application = Application(listing_id=listing.id, guest_id=guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=guest.id, status="ACCEPTED")
    db.add(offer)
    db.flush()
    agreement = Agreement(offer_id=offer.id, status="SIGNED")
    db.add(agreement)
    db.flush()
    occupancy = Occupancy(
        offer_id=offer.id, listing_id=listing.id, room_id=room.id, guest_id=guest.id,
        status="ACTIVE", move_in_date=date.today() - timedelta(days=10),
        expected_end_date=date.today() + timedelta(days=300),
    )
    db.add(occupancy)
    db.commit()
    return occupancy, renter_user, host_user


class TestRequestMoveOut:
    def test_renter_can_request_move_out(self, client, db_session: Session):
        occupancy, renter, host_user = _make_active_occupancy(db_session)
        desired_date = (date.today() + timedelta(days=30)).isoformat()

        resp = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/request-move-out",
            json={"desiredMoveOutDate": desired_date},
            cookies=auth_user_cookie(renter),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["requestedMoveOutDate"] == desired_date
        assert body["moveOutRequestedAt"] is not None

        host_notifs = notif_crud.list_for_user(db_session, host_user.id)
        assert any(n.notification_type == "occupancy.move_out_requested" for n in host_notifs)

    def test_notifies_super_admins_too(self, client, db_session: Session):
        occupancy, renter, _host_user = _make_active_occupancy(db_session)
        admin = _make_admin(db_session, email="vacate-admin@test.com", role="super_admin")
        db_session.commit()

        resp = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/request-move-out",
            json={"desiredMoveOutDate": (date.today() + timedelta(days=30)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        assert resp.status_code == 200

        admin_notifs = notif_crud.list_for_admin(db_session, admin.id)
        assert any(n.notification_type == "occupancy.move_out_requested" for n in admin_notifs)

    def test_cannot_request_move_out_for_someone_elses_occupancy(self, client, db_session: Session):
        occupancy, _renter, _host_user = _make_active_occupancy(db_session)
        stranger = _make_user(db_session, email="vacate-stranger@test.com")
        db_session.commit()

        resp = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/request-move-out",
            json={"desiredMoveOutDate": (date.today() + timedelta(days=30)).isoformat()},
            cookies=auth_user_cookie(stranger),
        )
        assert resp.status_code == 403

    def test_past_date_is_rejected(self, client, db_session: Session):
        occupancy, renter, _host_user = _make_active_occupancy(db_session)

        resp = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/request-move-out",
            json={"desiredMoveOutDate": (date.today() - timedelta(days=1)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        assert resp.status_code == 400

    def test_cannot_request_for_non_active_occupancy(self, client, db_session: Session):
        occupancy, renter, _host_user = _make_active_occupancy(db_session)
        occupancy.status = "ENDED"
        db_session.commit()

        resp = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/request-move-out",
            json={"desiredMoveOutDate": (date.today() + timedelta(days=30)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        assert resp.status_code == 409

    def test_missing_occupancy_is_404(self, client, db_session: Session):
        renter = _make_user(db_session, email="vacate-renter2@test.com")
        db_session.commit()

        resp = client.post(
            "/api/users/rentals/occupancies/999999/request-move-out",
            json={"desiredMoveOutDate": (date.today() + timedelta(days=30)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        assert resp.status_code == 404
