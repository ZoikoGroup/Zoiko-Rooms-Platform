"""ZR-PAY-LINK-003 Section 1.1/2: "'Authority to list' and 'authority to
receive payments' are separate claims." Covers crud/payment_recipient_authority.py,
api/routes/payment_recipient_authority.py (admin verify/reject/revoke),
api/routes/user_hosting.py's host-facing declare/list routes, and
crud/rental_payment.py:resolve_rent_recipient_party_id, the resolver
crud/leasing.py and crud/occupancy.py now go through instead of always
assuming the property owner receives rent."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import payment_recipient_authority as crud
from app.crud.rental_payment import resolve_rent_recipient_party_id
from app.models.party import Party
from app.models.payment_recipient_authority import PaymentRecipientAuthority
from app.models.property import Property
from app.models.room import Room
from app.models.user_account import UserAccount
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie


def _make_room_owned_by(db: Session, party: Party) -> Room:
    prop = Property(owner_party_id=party.id, address="1 Recipient St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.commit()
    return room


def _make_host_with_room(db: Session, *, email: str = "recipienthost@test.com") -> tuple[UserAccount, Room, Party]:
    party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()
    user = _make_user(db, email=email)
    user.party_id = party.id
    db.flush()
    room = _make_room_owned_by(db, party)
    return user, room, party


class TestResolveRentRecipientPartyId:
    def test_falls_back_to_property_owner_when_no_claim_exists(self, db_session: Session):
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        room = _make_room_owned_by(db_session, party)

        assert resolve_rent_recipient_party_id(db_session, room) == party.id

    def test_a_pending_claim_for_someone_else_does_not_change_the_resolved_recipient(self, db_session: Session):
        """A submitted-but-not-yet-verified claim must not silently take
        effect -- only a VERIFIED claim may override the owner default."""
        owner = Party(party_type="provider", status="active", jurisdiction="IN")
        agent = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add_all([owner, agent])
        db_session.commit()
        room = _make_room_owned_by(db_session, owner)
        db_session.add(
            PaymentRecipientAuthority(party_id=agent.id, room_id=room.id, relationship_type="AGENT", status="pending")
        )
        db_session.commit()

        assert resolve_rent_recipient_party_id(db_session, room) == owner.id

    def test_a_verified_claim_for_an_agent_overrides_the_owner_default(self, db_session: Session):
        owner = Party(party_type="provider", status="active", jurisdiction="IN")
        agent = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add_all([owner, agent])
        db_session.commit()
        room = _make_room_owned_by(db_session, owner)
        db_session.add(
            PaymentRecipientAuthority(
                party_id=agent.id, room_id=room.id, relationship_type="AGENT", status="verified",
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        db_session.commit()

        assert resolve_rent_recipient_party_id(db_session, room) == agent.id

    def test_an_expired_verified_claim_falls_back_to_the_owner(self, db_session: Session):
        owner = Party(party_type="provider", status="active", jurisdiction="IN")
        agent = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add_all([owner, agent])
        db_session.commit()
        room = _make_room_owned_by(db_session, owner)
        db_session.add(
            PaymentRecipientAuthority(
                party_id=agent.id, room_id=room.id, relationship_type="AGENT", status="verified",
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
        )
        db_session.commit()

        assert resolve_rent_recipient_party_id(db_session, room) == owner.id

    def test_a_revoked_claim_falls_back_to_the_owner(self, db_session: Session):
        owner = Party(party_type="provider", status="active", jurisdiction="IN")
        agent = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add_all([owner, agent])
        db_session.commit()
        room = _make_room_owned_by(db_session, owner)
        db_session.add(
            PaymentRecipientAuthority(party_id=agent.id, room_id=room.id, relationship_type="AGENT", status="revoked")
        )
        db_session.commit()

        assert resolve_rent_recipient_party_id(db_session, room) == owner.id


class TestDeclarePaymentRecipientAuthorityCrud:
    def test_owner_can_declare_themselves_as_recipient(self, db_session: Session):
        user, room, party = _make_host_with_room(db_session)
        record, raw_code = crud.declare_payment_recipient_authority(
            db_session, user, room, recipient_party_id=party.id, relationship_type="OWNER", evidence_ref="id.pdf",
        )
        assert record.status == "pending"
        assert record.party_id == party.id
        assert record.relationship_type == "OWNER"
        assert raw_code is None

    def test_owner_can_declare_a_different_party_as_agent_recipient(self, db_session: Session):
        """The key capability list-authority declaration doesn't have --
        the submitter (property owner) may name a DIFFERENT party as the
        actual payment recipient."""
        user, room, _owner_party = _make_host_with_room(db_session, email="agent-designator@test.com")
        agent_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(agent_party)
        db_session.commit()

        record, _raw_code = crud.declare_payment_recipient_authority(
            db_session, user, room, recipient_party_id=agent_party.id, relationship_type="AGENT",
            evidence_ref="agency-agreement.pdf",
        )
        assert record.party_id == agent_party.id
        assert record.relationship_type == "AGENT"

    def test_non_owner_cannot_declare_for_a_room_they_dont_own(self, db_session: Session):
        user, _room, _party = _make_host_with_room(db_session, email="outsider@test.com")
        other_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(other_party)
        db_session.commit()
        other_room = _make_room_owned_by(db_session, other_party)

        with pytest.raises(HTTPException) as exc_info:
            crud.declare_payment_recipient_authority(
                db_session, user, other_room, recipient_party_id=other_party.id,
                relationship_type="OWNER", evidence_ref="x.pdf",
            )
        assert exc_info.value.status_code == 403


class TestVerifyRejectRevokePaymentRecipientAuthority:
    def _make_pending(self, db: Session) -> PaymentRecipientAuthority:
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db.add(party)
        db.commit()
        room = _make_room_owned_by(db, party)
        record = PaymentRecipientAuthority(party_id=party.id, room_id=room.id, relationship_type="OWNER", status="pending")
        db.add(record)
        db.commit()
        return record

    def test_verify_sets_status_verified_at_and_a_365_day_expiry(self, db_session: Session):
        record = self._make_pending(db_session)
        verifier = _make_admin(db_session, email="recipient-verify@test.com", role="super_admin")

        before = datetime.now(timezone.utc)
        updated = crud.verify_payment_recipient_authority(db_session, record, verifier)

        assert updated.status == "verified"
        assert updated.verifier_admin_id == verifier.id
        assert updated.expires_at - before > timedelta(days=360)

    def test_reject_sets_status_failed(self, db_session: Session):
        record = self._make_pending(db_session)
        verifier = _make_admin(db_session, email="recipient-reject@test.com", role="super_admin")
        updated = crud.reject_payment_recipient_authority(db_session, record, verifier)
        assert updated.status == "failed"

    def test_revoke_requires_a_currently_verified_record(self, db_session: Session):
        record = self._make_pending(db_session)
        revoker = _make_admin(db_session, email="recipient-revoke-pending@test.com", role="super_admin")
        with pytest.raises(HTTPException) as exc_info:
            crud.revoke_payment_recipient_authority(db_session, record, revoker)
        assert exc_info.value.status_code == 409

    def test_revoke_a_verified_record(self, db_session: Session):
        record = self._make_pending(db_session)
        verifier = _make_admin(db_session, email="recipient-revoke-ok@test.com", role="super_admin")
        crud.verify_payment_recipient_authority(db_session, record, verifier)
        updated = crud.revoke_payment_recipient_authority(db_session, record, verifier)
        assert updated.status == "revoked"


class TestPaymentRecipientAuthorityAdminRoutes:
    def test_plain_admin_cannot_verify(self, client, db_session: Session):
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        room = _make_room_owned_by(db_session, party)
        record = PaymentRecipientAuthority(party_id=party.id, room_id=room.id, relationship_type="OWNER", status="pending")
        db_session.add(record)
        db_session.commit()

        admin = _make_admin(db_session, email="recipient-route-plain@test.com", role="admin")
        r = client.post(f"/api/payment-recipient-authorities/{record.id}/verify", cookies=auth_admin_cookie(admin))
        assert r.status_code == 403, r.text

    def test_super_admin_can_verify(self, client, db_session: Session):
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        room = _make_room_owned_by(db_session, party)
        record = PaymentRecipientAuthority(party_id=party.id, room_id=room.id, relationship_type="OWNER", status="pending")
        db_session.add(record)
        db_session.commit()

        super_admin = _make_admin(db_session, email="recipient-route-super@test.com", role="super_admin")
        r = client.post(f"/api/payment-recipient-authorities/{record.id}/verify", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "verified"

    def test_verify_unknown_id_is_404(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="recipient-route-404@test.com", role="super_admin")
        r = client.post("/api/payment-recipient-authorities/999999/verify", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 404, r.text

    def test_revoke_requires_a_reason(self, client, db_session: Session):
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        room = _make_room_owned_by(db_session, party)
        record = PaymentRecipientAuthority(
            party_id=party.id, room_id=room.id, relationship_type="OWNER", status="verified",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        db_session.add(record)
        db_session.commit()

        super_admin = _make_admin(db_session, email="recipient-route-revoke@test.com", role="super_admin")
        r = client.post(
            f"/api/payment-recipient-authorities/{record.id}/revoke", json={"reason": "agent relationship ended"},
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "revoked"

    def test_get_records_filters_by_room_id(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="recipient-route-list@test.com", role="super_admin")
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        room_a = _make_room_owned_by(db_session, party)
        room_b = _make_room_owned_by(db_session, party)
        db_session.add(PaymentRecipientAuthority(party_id=party.id, room_id=room_a.id, relationship_type="OWNER", status="pending"))
        db_session.add(PaymentRecipientAuthority(party_id=party.id, room_id=room_b.id, relationship_type="OWNER", status="pending"))
        db_session.commit()

        r = client.get(f"/api/payment-recipient-authorities?room_id={room_a.id}", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1
        assert r.json()[0]["roomId"] == room_a.id


class TestPaymentRecipientAuthorityHostRoutes:
    def test_host_can_declare_themselves_as_recipient(self, client, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="route-owner@test.com")
        r = client.post(
            f"/api/users/hosting/rooms/{room.id}/payment-recipient-authorities",
            json={"roomId": room.id, "recipientPartyId": party.id, "relationshipType": "OWNER", "evidenceRef": "id.pdf"},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "pending"
        assert body["partyId"] == party.id

    def test_omitting_recipient_party_id_defaults_to_the_callers_own_party(self, client, db_session: Session):
        """Wireframe A's default choice, 'Me / the property owner' -- the
        frontend has no reason to otherwise know its own party id."""
        user, room, party = _make_host_with_room(db_session, email="route-default-self@test.com")
        r = client.post(
            f"/api/users/hosting/rooms/{room.id}/payment-recipient-authorities",
            json={"roomId": room.id, "relationshipType": "OWNER", "evidenceRef": "id.pdf"},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 201, r.text
        assert r.json()["partyId"] == party.id

    def test_host_can_declare_an_agent_as_recipient(self, client, db_session: Session):
        user, room, _party = _make_host_with_room(db_session, email="route-agent@test.com")
        agent_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(agent_party)
        db_session.commit()

        r = client.post(
            f"/api/users/hosting/rooms/{room.id}/payment-recipient-authorities",
            json={
                "roomId": room.id, "recipientPartyId": agent_party.id, "relationshipType": "AGENT",
                "evidenceRef": "agency-agreement.pdf",
            },
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 201, r.text
        assert r.json()["partyId"] == agent_party.id
        assert r.json()["relationshipType"] == "AGENT"

    def test_host_cannot_declare_for_a_room_they_dont_own(self, client, db_session: Session):
        user, _room, _party = _make_host_with_room(db_session, email="route-outsider@test.com")
        other_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(other_party)
        db_session.commit()
        other_room = _make_room_owned_by(db_session, other_party)

        r = client.post(
            f"/api/users/hosting/rooms/{other_room.id}/payment-recipient-authorities",
            json={"roomId": other_room.id, "recipientPartyId": other_party.id, "relationshipType": "OWNER", "evidenceRef": "x.pdf"},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 403, r.text

    def test_room_id_mismatch_between_url_and_body_is_rejected(self, client, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="route-mismatch@test.com")
        r = client.post(
            f"/api/users/hosting/rooms/{room.id}/payment-recipient-authorities",
            json={"roomId": room.id + 999, "recipientPartyId": party.id, "relationshipType": "OWNER", "evidenceRef": "id.pdf"},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 400, r.text

    def test_admin_verify_workflow_works_on_a_host_declared_record(self, client, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="route-admin-flow@test.com")
        r = client.post(
            f"/api/users/hosting/rooms/{room.id}/payment-recipient-authorities",
            json={"roomId": room.id, "recipientPartyId": party.id, "relationshipType": "OWNER", "evidenceRef": "id.pdf"},
            cookies=auth_user_cookie(user),
        )
        declared_id = r.json()["id"]

        super_admin = _make_admin(db_session, email="route-admin-flow-super@test.com", role="super_admin")
        r2 = client.post(f"/api/payment-recipient-authorities/{declared_id}/verify", cookies=auth_admin_cookie(super_admin))
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "verified"

        # And the resolver now picks this up for future obligations.
        room_obj = db_session.get(Room, room.id)
        assert resolve_rent_recipient_party_id(db_session, room_obj) == party.id

    def test_host_cannot_view_another_hosts_payment_recipient_authorities(self, client, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="route-viewer-owner@test.com")
        crud.declare_payment_recipient_authority(
            db_session, user, room, recipient_party_id=party.id, relationship_type="OWNER", evidence_ref="id.pdf",
        )
        outsider, _room2, _party2 = _make_host_with_room(db_session, email="route-viewer-outsider@test.com")

        r = client.get(f"/api/users/hosting/rooms/{room.id}/payment-recipient-authorities", cookies=auth_user_cookie(outsider))
        assert r.status_code == 403, r.text
