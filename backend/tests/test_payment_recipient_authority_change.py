"""ZR-PAY-LINK-003 Section 14.1/Wireframe J: recipient-change governance --
step-up authentication, risk flagging and affected-tenant notification for a
CHANGE to who receives rent for a room, layered on top of the existing
declare/verify workflow tested in test_payment_recipient_authority.py. A
room's first-ever declaration is deliberately unaffected -- see
crud/payment_recipient_authority.py:declare_payment_recipient_authority's own
docstring."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import payment_recipient_authority as crud
from app.crud import rental_payment as rp_crud
from app.models.guest import Guest
from app.models.notification import Notification
from app.models.party import Party
from app.models.payment_recipient_authority import PaymentRecipientAuthority
from app.models.property import Property
from app.models.room import Room
from app.models.user_account import UserAccount
from tests.conftest import _make_admin, _make_user, auth_user_cookie


def _make_room_owned_by(db: Session, party: Party) -> Room:
    prop = Property(owner_party_id=party.id, address="1 Change St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.commit()
    return room


def _make_host_with_room(db: Session, *, email: str = "changehost@test.com") -> tuple[UserAccount, Room, Party]:
    party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()
    user = _make_user(db, email=email)
    user.party_id = party.id
    db.flush()
    room = _make_room_owned_by(db, party)
    return user, room, party


def _declare_and_verify(db: Session, user: UserAccount, room: Room, party: Party) -> PaymentRecipientAuthority:
    """Gets a room to a live-verified state so the NEXT declare is a change."""
    record, _raw_code = crud.declare_payment_recipient_authority(
        db, user, room, recipient_party_id=party.id, relationship_type="OWNER", evidence_ref="id.pdf",
    )
    admin = _make_admin(db, email=f"verify-{room.id}@test.com", role="super_admin")
    return crud.verify_payment_recipient_authority(db, record, admin)


class TestDeclareChangeVsFirstDeclare:
    def test_first_declare_for_room_is_unaffected(self, db_session: Session):
        user, room, party = _make_host_with_room(db_session)
        record, raw_code = crud.declare_payment_recipient_authority(
            db_session, user, room, recipient_party_id=party.id, relationship_type="OWNER", evidence_ref="id.pdf",
        )
        assert record.status == "pending"
        assert raw_code is None
        assert record.verification_code_hash is None

    def test_declaring_a_change_requires_step_up(self, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="change-declare@test.com")
        _declare_and_verify(db_session, user, room, party)

        agent = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(agent)
        db_session.commit()

        record, raw_code = crud.declare_payment_recipient_authority(
            db_session, user, room, recipient_party_id=agent.id, relationship_type="AGENT", evidence_ref="agency.pdf",
        )
        assert record.status == "pending_step_up"
        assert raw_code is not None
        assert record.verification_code_hash is not None
        assert record.verification_code_expires_at is not None


class TestConfirmPaymentRecipientAuthorityChange:
    def _make_pending_change(self, db: Session, *, email: str = "confirm-change@test.com"):
        user, room, party = _make_host_with_room(db, email=email)
        _declare_and_verify(db, user, room, party)
        agent = Party(party_type="provider", status="active", jurisdiction="IN")
        db.add(agent)
        db.commit()
        record, raw_code = crud.declare_payment_recipient_authority(
            db, user, room, recipient_party_id=agent.id, relationship_type="AGENT", evidence_ref="agency.pdf",
        )
        return user, room, record, raw_code

    def test_wrong_code_increments_attempts_and_fails(self, db_session: Session):
        user, _room, record, _raw_code = self._make_pending_change(db_session)
        with pytest.raises(HTTPException) as exc:
            crud.confirm_payment_recipient_authority_change(db_session, record, user, "000000")
        assert exc.value.status_code == 400
        assert record.verification_attempts == 1

    def test_too_many_attempts_is_rejected(self, db_session: Session):
        user, _room, record, _raw_code = self._make_pending_change(db_session, email="too-many-attempts@test.com")
        for _ in range(crud.RECIPIENT_CHANGE_MAX_VERIFICATION_ATTEMPTS):
            with pytest.raises(HTTPException):
                crud.confirm_payment_recipient_authority_change(db_session, record, user, "000000")
        with pytest.raises(HTTPException) as exc:
            crud.confirm_payment_recipient_authority_change(db_session, record, user, "000000")
        assert exc.value.status_code == 429

    def test_expired_code_is_rejected(self, db_session: Session):
        user, _room, record, _raw_code = self._make_pending_change(db_session, email="expired-code@test.com")
        record.verification_code_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()
        with pytest.raises(HTTPException) as exc:
            crud.confirm_payment_recipient_authority_change(db_session, record, user, "000000")
        assert exc.value.status_code == 400

    def test_correct_code_moves_to_pending(self, db_session: Session):
        user, _room, record, raw_code = self._make_pending_change(db_session, email="correct-code@test.com")
        updated = crud.confirm_payment_recipient_authority_change(db_session, record, user, raw_code)
        assert updated.status == "pending"
        assert updated.is_high_risk is False

    def test_high_risk_change_still_only_reaches_pending(self, db_session: Session):
        user, _room, record, raw_code = self._make_pending_change(db_session, email="high-risk@test.com")
        user.password_changed_at = datetime.now(timezone.utc)
        db_session.commit()

        updated = crud.confirm_payment_recipient_authority_change(db_session, record, user, raw_code)
        assert updated.status == "pending"
        assert updated.is_high_risk is True
        assert updated.high_risk_reason


class TestVerifyPaymentRecipientAuthorityGuard:
    def test_verify_rejects_a_pending_step_up_row(self, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="guard@test.com")
        _declare_and_verify(db_session, user, room, party)
        agent = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(agent)
        db_session.commit()
        record, _raw_code = crud.declare_payment_recipient_authority(
            db_session, user, room, recipient_party_id=agent.id, relationship_type="AGENT", evidence_ref="agency.pdf",
        )
        assert record.status == "pending_step_up"

        admin = _make_admin(db_session, email="guard-admin@test.com", role="super_admin")
        with pytest.raises(HTTPException) as exc:
            crud.verify_payment_recipient_authority(db_session, record, admin)
        assert exc.value.status_code == 409


class TestVerifyNotifiesAffectedTenants:
    def _link_guest_to_user(self, db: Session, *, email: str) -> tuple[Guest, UserAccount]:
        tenant_user = _make_user(db, email=email)
        guest = Guest(id=f"G-{email}", name="Affected Tenant", email=email, joined_at=date.today())
        db.add(guest)
        db.flush()
        guest.user_account_id = tenant_user.id
        db.commit()
        return guest, tenant_user

    def test_genuine_change_notifies_the_previous_recipients_tenants(self, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="notify-owner@test.com")
        verified = _declare_and_verify(db_session, user, room, party)

        guest, tenant_user = self._link_guest_to_user(db_session, email="notify-tenant@test.com")
        rp_crud.create_obligation(
            db_session, obligation_type="RENT", tenant_guest_id=guest.id, recipient_party_id=verified.party_id,
            amount=850, currency="GBP", due_date=date.today(),
        )

        agent = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(agent)
        db_session.commit()
        record, raw_code = crud.declare_payment_recipient_authority(
            db_session, user, room, recipient_party_id=agent.id, relationship_type="AGENT", evidence_ref="agency.pdf",
        )
        crud.confirm_payment_recipient_authority_change(db_session, record, user, raw_code)

        admin = _make_admin(db_session, email="notify-admin@test.com", role="super_admin")
        crud.verify_payment_recipient_authority(db_session, record, admin)

        notifications = db_session.query(Notification).filter_by(
            recipient_user_id=tenant_user.id, notification_type="payment_recipient_authority.changed",
        ).all()
        assert len(notifications) == 1

    def test_first_ever_verification_does_not_notify(self, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="no-notify-owner@test.com")
        record, _raw_code = crud.declare_payment_recipient_authority(
            db_session, user, room, recipient_party_id=party.id, relationship_type="OWNER", evidence_ref="id.pdf",
        )
        guest, tenant_user = self._link_guest_to_user(db_session, email="no-notify-tenant@test.com")
        rp_crud.create_obligation(
            db_session, obligation_type="RENT", tenant_guest_id=guest.id, recipient_party_id=party.id,
            amount=850, currency="GBP", due_date=date.today(),
        )

        admin = _make_admin(db_session, email="no-notify-admin@test.com", role="super_admin")
        crud.verify_payment_recipient_authority(db_session, record, admin)

        notifications = db_session.query(Notification).filter_by(
            recipient_user_id=tenant_user.id, notification_type="payment_recipient_authority.changed",
        ).all()
        assert len(notifications) == 0


class TestPaymentRecipientAuthorityChangeRoutes:
    def test_owner_can_resend_and_confirm_change_code(self, client, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="route-owner@test.com")
        _declare_and_verify(db_session, user, room, party)
        agent = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(agent)
        db_session.commit()

        # Declared via crud directly (not the HTTP route) purely to obtain
        # raw_code for this test -- the API itself never returns it, only
        # mails it (Section 14.1: the code must never be observable except
        # by the account it was sent to).
        record, raw_code = crud.declare_payment_recipient_authority(
            db_session, user, room, recipient_party_id=agent.id, relationship_type="AGENT", evidence_ref="agency.pdf",
        )
        assert record.status == "pending_step_up"

        r_resend = client.post(
            f"/api/users/hosting/rooms/{room.id}/payment-recipient-authorities/{record.id}/resend-change-code",
            cookies=auth_user_cookie(user),
        )
        assert r_resend.status_code == 200, r_resend.text

        r_confirm = client.post(
            f"/api/users/hosting/rooms/{room.id}/payment-recipient-authorities/{record.id}/confirm-change",
            json={"code": "000000"},
            cookies=auth_user_cookie(user),
        )
        # resend rotated the code, so the ORIGINAL raw_code is now stale --
        # asserts the wrong-code path (400), same coverage as
        # TestConfirmPaymentRecipientAuthorityChange.test_wrong_code_increments_attempts_and_fails
        # but exercised through the route for the resend+confirm wiring itself.
        assert r_confirm.status_code == 400, r_confirm.text

    def test_confirm_change_route_succeeds_with_the_correct_code(self, client, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="route-owner-confirm@test.com")
        _declare_and_verify(db_session, user, room, party)
        agent = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(agent)
        db_session.commit()
        record, raw_code = crud.declare_payment_recipient_authority(
            db_session, user, room, recipient_party_id=agent.id, relationship_type="AGENT", evidence_ref="agency.pdf",
        )

        r = client.post(
            f"/api/users/hosting/rooms/{room.id}/payment-recipient-authorities/{record.id}/confirm-change",
            json={"code": raw_code},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "pending"

    def test_non_owner_cannot_confirm_another_hosts_change(self, client, db_session: Session):
        user, room, party = _make_host_with_room(db_session, email="route-owner-2@test.com")
        verified = _declare_and_verify(db_session, user, room, party)
        agent = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(agent)
        db_session.commit()
        record, _raw_code = crud.declare_payment_recipient_authority(
            db_session, user, room, recipient_party_id=agent.id, relationship_type="AGENT", evidence_ref="agency.pdf",
        )

        outsider, _room2, _party2 = _make_host_with_room(db_session, email="route-outsider@test.com")
        r = client.post(
            f"/api/users/hosting/rooms/{room.id}/payment-recipient-authorities/{record.id}/confirm-change",
            json={"code": "123456"},
            cookies=auth_user_cookie(outsider),
        )
        assert r.status_code == 403, r.text
