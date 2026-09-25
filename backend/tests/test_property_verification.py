"""Lister, Property & Authority Verification wireframe -- PropertyVerification
(crud/property_verification.py, api/routes/user_hosting.py's
/rooms/{room_id}/property-verifications, api/routes/verification.py's
/property-verifications/* admin routes). Deliberately a separate model from
PropertyComplianceCredential (per-jurisdiction regulatory documents) and from
AuthorityRecord (right to list) -- this is "is the property/address itself
real and evidenced." Mirrors test_authority_records.py's own coverage shape."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import property_verification as crud
from app.models.party import Party
from app.models.property import Property
from app.models.property_verification import PropertyVerification
from app.models.room import Room
from app.models.user_account import UserAccount
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie

_PDF_BYTES = b"%PDF-1.4 fake property evidence content"


def _declare(db: Session, user: UserAccount, room: Room, *, evidence_ref: str = "deed.pdf") -> PropertyVerification:
    """declare_property_verification now requires a real uploaded document
    alongside evidence_ref -- same file-metadata shape
    save_property_verification_document returns."""
    return crud.declare_property_verification(
        db, user, room, evidence_ref=evidence_ref,
        stored_filename="test-stored.pdf", original_filename="deed.pdf",
        content_type="application/pdf", file_size=len(_PDF_BYTES),
    )


def _make_host_with_room(db: Session, *, email: str = "pvhost@test.com") -> tuple[UserAccount, Room]:
    party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()
    user = _make_user(db, email=email)
    user.party_id = party.id
    db.flush()
    prop = Property(owner_party_id=party.id, address="1 Verify Way", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.commit()
    return user, room


class TestDeclarePropertyVerificationCrud:
    def test_declare_creates_a_pending_record(self, db_session: Session):
        user, room = _make_host_with_room(db_session)
        record = _declare(db_session, user, room, evidence_ref="title-deed.pdf")
        assert record.status == "pending"
        assert record.party_id == user.party_id
        assert record.room_id == room.id
        assert record.evidence_ref == "title-deed.pdf"

    def test_host_cannot_declare_for_a_room_they_dont_own(self, db_session: Session):
        user, _room = _make_host_with_room(db_session, email="pv-outsider@test.com")
        other_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(other_party)
        db_session.commit()
        other_prop = Property(owner_party_id=other_party.id, address="9 Other Rd", city="Bengaluru", status="active")
        db_session.add(other_prop)
        db_session.flush()
        other_room = Room(property_id=other_prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
        db_session.add(other_room)
        db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            _declare(db_session, user, other_room, evidence_ref="x.pdf")
        assert exc_info.value.status_code == 403

    def test_declare_rejects_blank_evidence_ref(self, db_session: Session):
        user, room = _make_host_with_room(db_session, email="pv-blank@test.com")
        with pytest.raises(HTTPException) as exc_info:
            _declare(db_session, user, room, evidence_ref="   ")
        assert exc_info.value.status_code == 400


class TestVerifyRejectRequestEvidenceRevoke:
    def _make_pending(self, db: Session) -> PropertyVerification:
        user, room = _make_host_with_room(db, email=f"pv-lifecycle-{id(db)}@test.com")
        return _declare(db, user, room)

    def test_verify_sets_status_verified_at_and_a_365_day_expiry(self, db_session: Session):
        record = self._make_pending(db_session)
        admin = _make_admin(db_session, email="pv-verify@test.com", role="super_admin")

        before = datetime.now(timezone.utc)
        updated = crud.verify_property_verification(db_session, record, admin)

        assert updated.status == "verified"
        assert updated.verifier_admin_id == admin.id
        assert updated.verified_at is not None
        assert updated.expires_at - before > timedelta(days=360)

    def test_verify_rejects_an_already_rejected_record(self, db_session: Session):
        record = self._make_pending(db_session)
        admin = _make_admin(db_session, email="pv-verify-conflict@test.com", role="super_admin")
        crud.reject_property_verification(db_session, record, admin)
        with pytest.raises(HTTPException) as exc_info:
            crud.verify_property_verification(db_session, record, admin)
        assert exc_info.value.status_code == 409

    def test_reject_sets_status_rejected_with_notes(self, db_session: Session):
        record = self._make_pending(db_session)
        admin = _make_admin(db_session, email="pv-reject@test.com", role="super_admin")
        updated = crud.reject_property_verification(db_session, record, admin, notes="Blurry photo")
        assert updated.status == "rejected"
        assert updated.verifier_notes == "Blurry photo"

    def test_request_additional_evidence_sets_manual_review_state(self, db_session: Session):
        record = self._make_pending(db_session)
        admin = _make_admin(db_session, email="pv-more-evidence@test.com", role="super_admin")
        updated = crud.request_additional_property_evidence(db_session, record, admin, notes="Need utility bill too")
        assert updated.status == "additional_evidence_required"
        assert updated.verifier_notes == "Need utility bill too"

    def test_revoke_requires_a_currently_verified_record(self, db_session: Session):
        record = self._make_pending(db_session)
        admin = _make_admin(db_session, email="pv-revoke-conflict@test.com", role="super_admin")
        with pytest.raises(HTTPException) as exc_info:
            crud.revoke_property_verification(db_session, record, admin, reason="fraud")
        assert exc_info.value.status_code == 409

    def test_revoke_a_verified_record(self, db_session: Session):
        record = self._make_pending(db_session)
        admin = _make_admin(db_session, email="pv-revoke@test.com", role="super_admin")
        crud.verify_property_verification(db_session, record, admin)
        updated = crud.revoke_property_verification(db_session, record, admin, reason="Evidence found fraudulent")
        assert updated.status == "revoked"
        assert updated.verifier_notes == "Evidence found fraudulent"


class TestGetValidPropertyVerificationForRoom:
    def test_none_when_no_record_exists(self, db_session: Session):
        assert crud.get_valid_property_verification_for_room(db_session, 999999) is None

    def test_none_for_a_pending_record(self, db_session: Session):
        user, room = _make_host_with_room(db_session, email="pv-valid-pending@test.com")
        _declare(db_session, user, room)
        assert crud.get_valid_property_verification_for_room(db_session, room.id) is None

    def test_none_for_an_expired_record(self, db_session: Session):
        user, room = _make_host_with_room(db_session, email="pv-valid-expired@test.com")
        db_session.add(
            PropertyVerification(
                party_id=user.party_id, room_id=room.id, evidence_ref="deed.pdf", status="verified",
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
        )
        db_session.commit()
        assert crud.get_valid_property_verification_for_room(db_session, room.id) is None

    def test_returns_a_currently_valid_record(self, db_session: Session):
        user, room = _make_host_with_room(db_session, email="pv-valid-current@test.com")
        record = PropertyVerification(
            party_id=user.party_id, room_id=room.id, evidence_ref="deed.pdf", status="verified",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        db_session.add(record)
        db_session.commit()
        found = crud.get_valid_property_verification_for_room(db_session, room.id)
        assert found is not None
        assert found.id == record.id


class TestPropertyVerificationRoutes:
    def test_host_can_submit_evidence_for_their_own_room(self, client, db_session: Session):
        user, room = _make_host_with_room(db_session, email="pv-route-owner@test.com")
        r = client.post(
            f"/api/users/hosting/rooms/{room.id}/property-verifications",
            data={"evidence_ref": "deed.pdf"},
            files={"file": ("deed.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "pending"
        assert r.json()["hasDocument"] is True

    def test_host_cannot_submit_for_a_room_they_dont_own(self, client, db_session: Session):
        user, _room = _make_host_with_room(db_session, email="pv-route-outsider@test.com")
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
            f"/api/users/hosting/rooms/{other_room.id}/property-verifications",
            data={"evidence_ref": "deed.pdf"},
            files={"file": ("deed.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 403, r.text

    def test_host_cannot_view_another_hosts_property_verifications(self, client, db_session: Session):
        user, room = _make_host_with_room(db_session, email="pv-route-viewer-owner@test.com")
        _declare(db_session, user, room)
        outsider, _ = _make_host_with_room(db_session, email="pv-route-viewer-outsider@test.com")

        r = client.get(f"/api/users/hosting/rooms/{room.id}/property-verifications", cookies=auth_user_cookie(outsider))
        assert r.status_code == 403, r.text

    def test_admin_can_verify_a_host_declared_record(self, client, db_session: Session):
        user, room = _make_host_with_room(db_session, email="pv-route-admin-verify@test.com")
        declared = _declare(db_session, user, room)
        super_admin = _make_admin(db_session, email="pv-route-super@test.com", role="super_admin")

        r = client.post(
            f"/api/verification/property-verifications/{declared.id}/verify", cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "verified"

    def test_plain_admin_cannot_verify(self, client, db_session: Session):
        user, room = _make_host_with_room(db_session, email="pv-route-plain@test.com")
        declared = _declare(db_session, user, room)
        admin = _make_admin(db_session, email="pv-route-plain-admin@test.com", role="admin")

        r = client.post(
            f"/api/verification/property-verifications/{declared.id}/verify", cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 403, r.text

    def test_admin_can_reject_with_notes(self, client, db_session: Session):
        user, room = _make_host_with_room(db_session, email="pv-route-reject@test.com")
        declared = _declare(db_session, user, room)
        super_admin = _make_admin(db_session, email="pv-route-reject-super@test.com", role="super_admin")

        r = client.post(
            f"/api/verification/property-verifications/{declared.id}/reject",
            json={"notes": "Address mismatch"},
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "rejected"

    def test_admin_can_request_additional_evidence(self, client, db_session: Session):
        user, room = _make_host_with_room(db_session, email="pv-route-more-evidence@test.com")
        declared = _declare(db_session, user, room)
        super_admin = _make_admin(db_session, email="pv-route-more-evidence-super@test.com", role="super_admin")

        r = client.post(
            f"/api/verification/property-verifications/{declared.id}/request-additional-evidence",
            json={"notes": "Need a second document"},
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "additional_evidence_required"

    def test_admin_can_revoke_a_verified_record(self, client, db_session: Session):
        user, room = _make_host_with_room(db_session, email="pv-route-revoke@test.com")
        declared = _declare(db_session, user, room)
        super_admin = _make_admin(db_session, email="pv-route-revoke-super@test.com", role="super_admin")
        client.post(f"/api/verification/property-verifications/{declared.id}/verify", cookies=auth_admin_cookie(super_admin))

        r = client.post(
            f"/api/verification/property-verifications/{declared.id}/revoke",
            json={"reason": "Evidence later found fraudulent"},
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "revoked"

    def test_admin_can_list_verifications_for_a_room(self, client, db_session: Session):
        user, room = _make_host_with_room(db_session, email="pv-route-list@test.com")
        _declare(db_session, user, room)
        admin = _make_admin(db_session, email="pv-route-list-admin@test.com", role="admin")

        r = client.get(f"/api/verification/property-verifications/room/{room.id}", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1


class TestAuditEventsAreLoggedOnce:
    """Regression coverage for a duplicate-audit-logging bug introduced and
    fixed during this feature's implementation: the admin decision routes
    (verify/reject/request-additional-evidence/revoke) must each log exactly
    one audit event, not two -- matching authority.py's own convention of
    logging admin decisions at the route layer only."""

    def test_verify_logs_exactly_one_audit_event(self, client, db_session: Session):
        from app.models.audit import AuditEvent

        user, room = _make_host_with_room(db_session, email="pv-audit-verify@test.com")
        declared = _declare(db_session, user, room)
        super_admin = _make_admin(db_session, email="pv-audit-verify-super@test.com", role="super_admin")

        client.post(f"/api/verification/property-verifications/{declared.id}/verify", cookies=auth_admin_cookie(super_admin))

        count = db_session.query(AuditEvent).filter(
            AuditEvent.action == "property_verification.verify", AuditEvent.resource_id == str(declared.id),
        ).count()
        assert count == 1
