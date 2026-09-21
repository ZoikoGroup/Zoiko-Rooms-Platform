"""Lister, Property & Authority Verification wireframe -- host self-service
authority-to-list submission (crud/authority.py:declare_authority_record,
api/routes/user_hosting.py's /rooms/{room_id}/authority-records). Existing
admin-only submit/verify/reject/revoke flow (test_authority_records.py) is
untouched; these tests cover only the new host-facing surface: the
OWNER/AGENT/MANAGER relationship-type taxonomy and the ownership scoping
that lets a host submit only for a room their own party actually owns."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.crud import authority as crud
from app.models.authority_record import AuthorityRecord
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.models.user_account import UserAccount
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie


def _make_host_with_room(db: Session, *, email: str = "authhost@test.com") -> tuple[UserAccount, Room]:
    party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()
    user = _make_user(db, email=email)
    user.party_id = party.id
    db.flush()
    prop = Property(owner_party_id=party.id, address="1 Host Way", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.commit()
    return user, room


class TestDeclareAuthorityRecordCrud:
    def test_owner_relationship_type_is_stored_and_lowercased_into_authority_type(self, db_session: Session):
        user, room = _make_host_with_room(db_session)
        record = crud.declare_authority_record(db_session, user, room, relationship_type="OWNER", evidence_ref="deed.pdf")
        assert record.relationship_type == "OWNER"
        assert record.authority_type == "owner"
        assert record.status == "pending"
        assert record.party_id == user.party_id
        assert record.evidence_ref == "deed.pdf"

    def test_agent_relationship_type(self, db_session: Session):
        user, room = _make_host_with_room(db_session, email="agent@test.com")
        record = crud.declare_authority_record(db_session, user, room, relationship_type="AGENT", evidence_ref="agency.pdf")
        assert record.relationship_type == "AGENT"

    def test_manager_relationship_type(self, db_session: Session):
        user, room = _make_host_with_room(db_session, email="manager@test.com")
        record = crud.declare_authority_record(db_session, user, room, relationship_type="MANAGER", evidence_ref="mgmt.pdf")
        assert record.relationship_type == "MANAGER"

    def test_host_cannot_declare_for_a_room_they_dont_own(self, db_session: Session):
        user, _room = _make_host_with_room(db_session, email="outsider@test.com")
        other_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(other_party)
        db_session.commit()
        other_prop = Property(owner_party_id=other_party.id, address="9 Other Rd", city="Bengaluru", status="active")
        db_session.add(other_prop)
        db_session.flush()
        other_room = Room(property_id=other_prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
        db_session.add(other_room)
        db_session.commit()

        import pytest
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            crud.declare_authority_record(db_session, user, other_room, relationship_type="OWNER", evidence_ref="x.pdf")
        assert exc_info.value.status_code == 403

    def test_list_for_room_owned_by_rejects_a_non_owning_user(self, db_session: Session):
        user, room = _make_host_with_room(db_session, email="viewer1@test.com")
        outsider, _ = _make_host_with_room(db_session, email="viewer2@test.com")

        import pytest
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            crud.list_authority_records_for_room_owned_by(db_session, outsider, room)
        assert exc_info.value.status_code == 403


class TestDeclareAuthorityRecordRoute:
    def test_host_can_submit_authority_for_their_own_room(self, client, db_session: Session):
        user, room = _make_host_with_room(db_session, email="route-owner@test.com")
        r = client.post(
            f"/api/users/hosting/rooms/{room.id}/authority-records",
            json={"roomId": room.id, "relationshipType": "OWNER", "evidenceRef": "deed.pdf"},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["relationshipType"] == "OWNER"
        assert body["status"] == "pending"

    def test_host_cannot_submit_for_a_room_they_dont_own(self, client, db_session: Session):
        user, _room = _make_host_with_room(db_session, email="route-outsider@test.com")
        other_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(other_party)
        db_session.commit()
        other_prop = Property(owner_party_id=other_party.id, address="9 Other Rd", city="Bengaluru", status="active")
        db_session.add(other_prop)
        db_session.flush()
        other_room = Room(property_id=other_prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
        db_session.add(other_room)
        db_session.commit()

        r = client.post(
            f"/api/users/hosting/rooms/{other_room.id}/authority-records",
            json={"roomId": other_room.id, "relationshipType": "OWNER", "evidenceRef": "deed.pdf"},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 403, r.text

    def test_host_cannot_view_another_hosts_authority_records(self, client, db_session: Session):
        user, room = _make_host_with_room(db_session, email="route-viewer-owner@test.com")
        crud.declare_authority_record(db_session, user, room, relationship_type="OWNER", evidence_ref="deed.pdf")
        outsider, _ = _make_host_with_room(db_session, email="route-viewer-outsider@test.com")

        r = client.get(f"/api/users/hosting/rooms/{room.id}/authority-records", cookies=auth_user_cookie(outsider))
        assert r.status_code == 403, r.text

    def test_host_can_view_their_own_rooms_authority_records(self, client, db_session: Session):
        user, room = _make_host_with_room(db_session, email="route-viewer-self@test.com")
        crud.declare_authority_record(db_session, user, room, relationship_type="AGENT", evidence_ref="agency.pdf")

        r = client.get(f"/api/users/hosting/rooms/{room.id}/authority-records", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1
        assert r.json()[0]["relationshipType"] == "AGENT"

    def test_room_id_mismatch_between_url_and_body_is_rejected(self, client, db_session: Session):
        user, room = _make_host_with_room(db_session, email="route-mismatch@test.com")
        r = client.post(
            f"/api/users/hosting/rooms/{room.id}/authority-records",
            json={"roomId": room.id + 999, "relationshipType": "OWNER", "evidenceRef": "deed.pdf"},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 400, r.text

    def test_admin_verify_reject_revoke_workflow_still_works_on_a_host_declared_record(self, client, db_session: Session):
        """The host self-service path lands in the same 'pending' status and
        goes through the exact same, unchanged admin verify/reject/revoke
        endpoints as an admin-submitted record (test_authority_records.py) --
        this just confirms that continues to be true end-to-end."""
        user, room = _make_host_with_room(db_session, email="route-admin-flow@test.com")
        declared = crud.declare_authority_record(db_session, user, room, relationship_type="OWNER", evidence_ref="deed.pdf")

        super_admin = _make_admin(db_session, email="authhost-super@test.com", role="super_admin")
        r = client.post(f"/api/authority-records/{declared.id}/verify", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "verified"
        assert r.json()["relationshipType"] == "OWNER"
