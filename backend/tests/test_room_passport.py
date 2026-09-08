"""Coverage for routes/room_passport.py -- per-room compliance/attribute
claims (e.g. "has fire extinguisher", evidence tier self_attested vs
verified). Zero test references before this despite being a real endpoint."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin, auth_admin_cookie


def _make_room(db: Session) -> Room:
    party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()
    prop = Property(owner_party_id=party.id, address="1 Passport St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.commit()
    return room


class TestRoomPassportClaims:
    def test_add_and_list_claims(self, client, db_session: Session):
        admin = _make_admin(db_session, email="passport-admin@test.com", role="super_admin")
        room = _make_room(db_session)
        db_session.commit()

        created = client.post(
            f"/api/rooms/{room.id}/passport/claims",
            json={"claimType": "fire_extinguisher", "value": "present", "evidenceTier": "self_attested"},
            cookies=auth_admin_cookie(admin),
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["roomId"] == room.id
        assert body["claimType"] == "fire_extinguisher"
        assert body["evidenceTier"] == "self_attested"

        listed = client.get(f"/api/rooms/{room.id}/passport/claims", cookies=auth_admin_cookie(admin))
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        assert listed.json()[0]["value"] == "present"

    def test_defaults_evidence_tier_to_self_attested(self, client, db_session: Session):
        admin = _make_admin(db_session, email="passport-admin2@test.com", role="super_admin")
        room = _make_room(db_session)
        db_session.commit()

        created = client.post(
            f"/api/rooms/{room.id}/passport/claims",
            json={"claimType": "smoke_detector", "value": "present"},
            cookies=auth_admin_cookie(admin),
        )
        assert created.status_code == 201
        assert created.json()["evidenceTier"] == "self_attested"

    def test_room_not_found_is_404(self, client, db_session: Session):
        admin = _make_admin(db_session, email="passport-admin3@test.com", role="super_admin")
        db_session.commit()

        resp = client.get("/api/rooms/999999/passport/claims", cookies=auth_admin_cookie(admin))
        assert resp.status_code == 404

    def test_requires_auth(self, client, db_session: Session):
        room = _make_room(db_session)
        db_session.commit()

        resp = client.get(f"/api/rooms/{room.id}/passport/claims")
        assert resp.status_code == 401
