"""Regression test for the room passport claims IDOR: get_claims/post_claim
previously had no ownership check at all, so any authenticated admin could
read or write passport claims for any room just by guessing room_id."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.membership import Membership
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin, auth_admin_cookie


def _make_room_owned_by(db: Session) -> tuple[Room, Party]:
    party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()
    prop = Property(owner_party_id=party.id, address="1 Test St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.commit()
    return room, party


class TestRoomPassportClaimsAreProviderScoped:
    def test_admin_without_membership_cannot_read_or_write_another_providers_room(
        self, client, db_session: Session
    ):
        room, _party = _make_room_owned_by(db_session)
        outsider_admin = _make_admin(db_session, email="outsider@test.com", role="admin")
        cookies = auth_admin_cookie(outsider_admin)

        r = client.get(f"/api/rooms/{room.id}/passport/claims", cookies=cookies)
        assert r.status_code == 403

        r = client.post(
            f"/api/rooms/{room.id}/passport/claims",
            json={"claimType": "step_free_access", "value": "true"},
            cookies=cookies,
        )
        assert r.status_code == 403

    def test_admin_with_membership_in_the_owning_party_can_read_and_write(self, db_session: Session, client):
        room, party = _make_room_owned_by(db_session)
        provider_admin = _make_admin(db_session, email="provider@test.com", role="admin")
        db_session.add(Membership(admin_user_id=provider_admin.id, party_id=party.id, role="provider_owner_admin"))
        db_session.commit()
        cookies = auth_admin_cookie(provider_admin)

        r = client.get(f"/api/rooms/{room.id}/passport/claims", cookies=cookies)
        assert r.status_code == 200
        assert r.json() == []

        r = client.post(
            f"/api/rooms/{room.id}/passport/claims",
            json={"claimType": "step_free_access", "value": "true"},
            cookies=cookies,
        )
        assert r.status_code == 201, r.text

    def test_super_admin_can_read_and_write_any_room(self, db_session: Session, client):
        room, _party = _make_room_owned_by(db_session)
        super_admin = _make_admin(db_session, email="super@test.com", role="super_admin")
        cookies = auth_admin_cookie(super_admin)

        r = client.get(f"/api/rooms/{room.id}/passport/claims", cookies=cookies)
        assert r.status_code == 200

        r = client.post(
            f"/api/rooms/{room.id}/passport/claims",
            json={"claimType": "step_free_access", "value": "true"},
            cookies=cookies,
        )
        assert r.status_code == 201, r.text
