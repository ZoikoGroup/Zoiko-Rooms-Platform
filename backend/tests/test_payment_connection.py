"""ZR-PAY-LINK-003 Section 3.1: the consolidated payment-connection status
view. Covers crud/payment_connection.py's state derivation and the
api/routes/user_hosting.py GET .../payment-connection route."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import payment_connection as crud
from app.models.party import Party
from app.models.payment_recipient_authority import PaymentRecipientAuthority
from app.models.property import Property
from app.models.rental_payment import RentalPaymentInstruction
from app.models.room import Room
from app.models.user_account import UserAccount
from tests.conftest import _make_user, auth_user_cookie


def _make_room_owned_by(db: Session, party: Party) -> Room:
    prop = Property(owner_party_id=party.id, address="1 Connection St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.commit()
    return room


def _make_host_with_room(db: Session, *, email: str = "connectionhost@test.com") -> tuple[UserAccount, Room, Party]:
    party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()
    user = _make_user(db, email=email)
    user.party_id = party.id
    db.flush()
    room = _make_room_owned_by(db, party)
    return user, room, party


def _make_active_instruction(db: Session, party_id: int, *, status: str = "ACTIVE") -> RentalPaymentInstruction:
    instruction = RentalPaymentInstruction(
        party_id=party_id, status=status, method="BANK_TRANSFER",
        recipient_name="Example Property Management Ltd", account_identifier_last4="8127",
    )
    db.add(instruction)
    db.commit()
    return instruction


class TestGetPaymentConnectionForRoom:
    def test_no_authority_declared_is_draft(self, db_session: Session):
        _user, room, _party = _make_host_with_room(db_session)
        connection = crud.get_payment_connection_for_room(db_session, room)
        assert connection.state == "DRAFT"
        assert connection.recipient_authority_id is None

    def test_pending_authority_is_pending_verification(self, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="pending@test.com")
        db_session.add(PaymentRecipientAuthority(party_id=party.id, room_id=room.id, relationship_type="OWNER", status="pending"))
        db_session.commit()

        connection = crud.get_payment_connection_for_room(db_session, room)
        assert connection.state == "PENDING_VERIFICATION"

    def test_verified_authority_with_no_destination_requires_recipient_setup(self, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="verified-no-dest@test.com")
        db_session.add(
            PaymentRecipientAuthority(
                party_id=party.id, room_id=room.id, relationship_type="OWNER", status="verified",
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        db_session.commit()

        connection = crud.get_payment_connection_for_room(db_session, room)
        assert connection.state == "RECIPIENT_SETUP_REQUIRED"
        assert connection.destination_method is None

    def test_verified_authority_with_active_destination_is_active(self, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="active@test.com")
        db_session.add(
            PaymentRecipientAuthority(
                party_id=party.id, room_id=room.id, relationship_type="OWNER", status="verified",
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        db_session.commit()
        _make_active_instruction(db_session, party.id)

        connection = crud.get_payment_connection_for_room(db_session, room)
        assert connection.state == "ACTIVE"
        assert connection.destination_account_identifier_masked == "******8127"

    def test_revoked_authority_is_suspended(self, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="revoked@test.com")
        db_session.add(PaymentRecipientAuthority(party_id=party.id, room_id=room.id, relationship_type="OWNER", status="revoked"))
        db_session.commit()

        connection = crud.get_payment_connection_for_room(db_session, room)
        assert connection.state == "SUSPENDED"

    def test_expired_verified_authority_is_suspended(self, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="expired@test.com")
        db_session.add(
            PaymentRecipientAuthority(
                party_id=party.id, room_id=room.id, relationship_type="OWNER", status="verified",
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
        )
        db_session.commit()

        connection = crud.get_payment_connection_for_room(db_session, room)
        assert connection.state == "SUSPENDED"

    def test_destination_pending_review_is_pending_verification(self, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="dest-review@test.com")
        db_session.add(
            PaymentRecipientAuthority(
                party_id=party.id, room_id=room.id, relationship_type="OWNER", status="verified",
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        db_session.commit()
        _make_active_instruction(db_session, party.id, status="PENDING_REVIEW")

        connection = crud.get_payment_connection_for_room(db_session, room)
        assert connection.state == "PENDING_VERIFICATION"

    def test_rejected_destination_is_suspended(self, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="dest-rejected@test.com")
        db_session.add(
            PaymentRecipientAuthority(
                party_id=party.id, room_id=room.id, relationship_type="OWNER", status="verified",
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        db_session.commit()
        _make_active_instruction(db_session, party.id, status="REJECTED")

        connection = crud.get_payment_connection_for_room(db_session, room)
        assert connection.state == "SUSPENDED"


class TestGetPaymentConnectionForRoomOwnedBy:
    def test_non_owner_gets_403(self, db_session: Session):
        user, room, _party = _make_host_with_room(db_session, email="owner@test.com")
        outsider, _room2, _party2 = _make_host_with_room(db_session, email="outsider@test.com")

        with pytest.raises(HTTPException) as exc_info:
            crud.get_payment_connection_for_room_owned_by(db_session, outsider, room)
        assert exc_info.value.status_code == 403


class TestPaymentConnectionRoute:
    def test_owner_can_view_their_own_connection(self, client, db_session: Session):
        user, room, _party = _make_host_with_room(db_session, email="route-owner@test.com")
        r = client.get(f"/api/users/hosting/rooms/{room.id}/payment-connection", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "DRAFT"

    def test_non_owner_cannot_view_another_hosts_connection(self, client, db_session: Session):
        _user, room, _party = _make_host_with_room(db_session, email="route-owner-2@test.com")
        outsider, _room2, _party2 = _make_host_with_room(db_session, email="route-outsider@test.com")

        r = client.get(f"/api/users/hosting/rooms/{room.id}/payment-connection", cookies=auth_user_cookie(outsider))
        assert r.status_code == 403, r.text

    def test_unknown_room_is_404(self, client, db_session: Session):
        user, _room, _party = _make_host_with_room(db_session, email="route-404@test.com")
        r = client.get("/api/users/hosting/rooms/999999/payment-connection", cookies=auth_user_cookie(user))
        assert r.status_code == 404, r.text
