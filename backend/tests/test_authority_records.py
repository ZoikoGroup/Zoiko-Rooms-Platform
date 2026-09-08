"""Unit + integration tests for authority records (crud/authority.py, 41%
covered; api/routes/authority.py, 50% covered) -- the verified-authority-record
gate that publish eligibility and payouts both depend on (see
crud/listing.py:check_publish_eligibility and crud/finance.py:run_payout).

Also covers an authorization gap found while writing these tests: unlike its
sibling room_passport.py (which gates claim submission with
assert_provider_access(db, admin, party_id_for_room(room))), authority.py's
POST endpoint let ANY authenticated admin submit an authority-record claim
against ANY room, not just one their own party owns.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.crud import authority as crud
from app.crud.party import get_or_create_default_party
from app.models.authority_record import AuthorityRecord
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.schemas.marketplace import AuthorityRecordCreate
from tests.conftest import _make_admin, auth_admin_cookie


def _make_room_owned_by(db: Session, party: Party) -> Room:
    prop = Property(owner_party_id=party.id, address="1 Authority St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.commit()
    return room


class TestSubmitAuthorityRecordCrud:
    def test_submit_creates_a_pending_record_tagged_with_the_submitters_own_party(self, db_session: Session):
        admin = _make_admin(db_session, email="auth-crud-submit@test.com", role="admin")
        owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(owner_party)
        db_session.commit()
        room = _make_room_owned_by(db_session, owner_party)

        record = crud.submit_authority_record(
            db_session, admin, room, AuthorityRecordCreate(room_id=room.id, authority_type="lease")
        )
        assert record.status == "pending"
        assert record.room_id == room.id
        assert record.party_id == get_or_create_default_party(db_session, admin).id


class TestVerifyAndRejectAuthorityRecord:
    def _make_pending(self, db: Session) -> AuthorityRecord:
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db.add(party)
        db.commit()
        room = _make_room_owned_by(db, party)
        record = AuthorityRecord(party_id=party.id, room_id=room.id, authority_type="lease", status="pending")
        db.add(record)
        db.commit()
        return record

    def test_verify_sets_status_verified_at_and_a_365_day_expiry(self, db_session: Session):
        record = self._make_pending(db_session)
        verifier = _make_admin(db_session, email="auth-verify@test.com", role="super_admin")

        before = datetime.now(timezone.utc)
        updated = crud.verify_authority_record(db_session, record, verifier)

        assert updated.status == "verified"
        assert updated.verifier_admin_id == verifier.id
        assert updated.verified_at is not None
        assert updated.expires_at - before > timedelta(days=360)

    def test_reject_sets_status_failed(self, db_session: Session):
        record = self._make_pending(db_session)
        verifier = _make_admin(db_session, email="auth-reject@test.com", role="super_admin")

        updated = crud.reject_authority_record(db_session, record, verifier)
        assert updated.status == "failed"
        assert updated.verifier_admin_id == verifier.id


class TestGetValidAuthorityForRoom:
    def test_none_when_no_record_exists(self, db_session: Session):
        assert crud.get_valid_authority_for_room(db_session, 999999) is None

    def test_none_for_a_pending_record(self, db_session: Session):
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        room = _make_room_owned_by(db_session, party)
        db_session.add(AuthorityRecord(party_id=party.id, room_id=room.id, authority_type="lease", status="pending"))
        db_session.commit()
        assert crud.get_valid_authority_for_room(db_session, room.id) is None

    def test_none_for_an_expired_record(self, db_session: Session):
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        room = _make_room_owned_by(db_session, party)
        db_session.add(
            AuthorityRecord(
                party_id=party.id, room_id=room.id, authority_type="lease", status="verified",
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
        )
        db_session.commit()
        assert crud.get_valid_authority_for_room(db_session, room.id) is None

    def test_returns_a_currently_valid_record(self, db_session: Session):
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        room = _make_room_owned_by(db_session, party)
        record = AuthorityRecord(
            party_id=party.id, room_id=room.id, authority_type="lease", status="verified",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        db_session.add(record)
        db_session.commit()
        found = crud.get_valid_authority_for_room(db_session, room.id)
        assert found is not None
        assert found.id == record.id

    def test_a_never_expiring_verified_record_is_valid(self, db_session: Session):
        """expires_at is nullable -- a NULL expiry must count as valid, not
        accidentally excluded by the expiry filter."""
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        room = _make_room_owned_by(db_session, party)
        record = AuthorityRecord(
            party_id=party.id, room_id=room.id, authority_type="lease", status="verified", expires_at=None,
        )
        db_session.add(record)
        db_session.commit()
        found = crud.get_valid_authority_for_room(db_session, room.id)
        assert found is not None


class TestAuthorityRecordRoutesRequireOwnership:
    def test_plain_admin_cannot_submit_a_claim_against_a_room_they_dont_own(self, client, db_session: Session):
        admin = _make_admin(db_session, email="auth-route-outsider@test.com", role="admin")
        other_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(other_party)
        db_session.commit()
        room = _make_room_owned_by(db_session, other_party)

        r = client.post(
            "/api/authority-records",
            json={"roomId": room.id, "authorityType": "lease"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 403, r.text

    def test_admin_can_submit_a_claim_for_their_own_room(self, client, db_session: Session):
        admin = _make_admin(db_session, email="auth-route-owner@test.com", role="admin")
        own_party = get_or_create_default_party(db_session, admin)
        room = _make_room_owned_by(db_session, own_party)

        r = client.post(
            "/api/authority-records",
            json={"roomId": room.id, "authorityType": "lease"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "pending"

    def test_super_admin_can_submit_for_any_room(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="auth-route-super@test.com", role="super_admin")
        other_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(other_party)
        db_session.commit()
        room = _make_room_owned_by(db_session, other_party)

        r = client.post(
            "/api/authority-records",
            json={"roomId": room.id, "authorityType": "lease"},
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 201, r.text

    def test_plain_admin_cannot_verify(self, client, db_session: Session):
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        room = _make_room_owned_by(db_session, party)
        record = AuthorityRecord(party_id=party.id, room_id=room.id, authority_type="lease", status="pending")
        db_session.add(record)
        db_session.commit()

        admin = _make_admin(db_session, email="auth-route-verify-plain@test.com", role="admin")
        r = client.post(f"/api/authority-records/{record.id}/verify", cookies=auth_admin_cookie(admin))
        assert r.status_code == 403, r.text

    def test_submit_against_an_unknown_room_is_404(self, client, db_session: Session):
        admin = _make_admin(db_session, email="auth-route-noroom@test.com", role="admin")
        r = client.post(
            "/api/authority-records", json={"roomId": 999999, "authorityType": "lease"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 404, r.text

    def test_super_admin_can_verify_a_record(self, client, db_session: Session):
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        room = _make_room_owned_by(db_session, party)
        record = AuthorityRecord(party_id=party.id, room_id=room.id, authority_type="lease", status="pending")
        db_session.add(record)
        db_session.commit()

        super_admin = _make_admin(db_session, email="auth-route-verify-super@test.com", role="super_admin")
        r = client.post(f"/api/authority-records/{record.id}/verify", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "verified"

    def test_verify_unknown_id_is_404(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="auth-route-verify-404@test.com", role="super_admin")
        r = client.post("/api/authority-records/999999/verify", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 404, r.text

    def test_super_admin_can_reject_a_record(self, client, db_session: Session):
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        room = _make_room_owned_by(db_session, party)
        record = AuthorityRecord(party_id=party.id, room_id=room.id, authority_type="lease", status="pending")
        db_session.add(record)
        db_session.commit()

        super_admin = _make_admin(db_session, email="auth-route-reject-super@test.com", role="super_admin")
        r = client.post(f"/api/authority-records/{record.id}/reject", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "failed"

    def test_plain_admin_cannot_reject(self, client, db_session: Session):
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        room = _make_room_owned_by(db_session, party)
        record = AuthorityRecord(party_id=party.id, room_id=room.id, authority_type="lease", status="pending")
        db_session.add(record)
        db_session.commit()

        admin = _make_admin(db_session, email="auth-route-reject-plain@test.com", role="admin")
        r = client.post(f"/api/authority-records/{record.id}/reject", cookies=auth_admin_cookie(admin))
        assert r.status_code == 403, r.text

    def test_reject_unknown_id_is_404(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="auth-route-reject-404@test.com", role="super_admin")
        r = client.post("/api/authority-records/999999/reject", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 404, r.text

    def test_get_records_filters_by_room_id(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="auth-route-list@test.com", role="super_admin")
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        room_a = _make_room_owned_by(db_session, party)
        room_b = _make_room_owned_by(db_session, party)
        db_session.add(AuthorityRecord(party_id=party.id, room_id=room_a.id, authority_type="lease", status="pending"))
        db_session.add(AuthorityRecord(party_id=party.id, room_id=room_b.id, authority_type="lease", status="pending"))
        db_session.commit()

        r = client.get(f"/api/authority-records?room_id={room_a.id}", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert all(row["roomId"] == room_a.id for row in r.json())
        assert len(r.json()) == 1
