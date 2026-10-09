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

    def test_returns_a_reviewer_verified_record(self, db_session: Session):
        """ZR-PROPERTY-VERIFY-001 P0 #2: a legacy per-room record counts only
        when a reviewer verified it -- never on a map hit alone."""
        from tests.conftest import _make_admin

        user, room = _make_host_with_room(db_session, email="pv-valid-current@test.com")
        unreviewed = PropertyVerification(
            party_id=user.party_id, room_id=room.id, evidence_ref="deed.pdf", status="verified",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        db_session.add(unreviewed)
        db_session.commit()
        assert crud.get_valid_property_verification_for_room(db_session, room.id) is None
        admin = _make_admin(db_session, email="pv-legacy-reviewer@test.com", role="super_admin")
        unreviewed.verifier_admin_id = admin.id
        db_session.commit()
        found = crud.get_valid_property_verification_for_room(db_session, room.id)
        assert found is not None and found.id == unreviewed.id


class TestPropertyVerificationRoutes:
    def test_per_room_submission_is_retired(self, client, db_session: Session):
        """Properties are verified once per property (ZR-PROPERTY-VERIFY-001);
        the old per-room upload, which auto-verified on a map hit, is gone."""
        user, room = _make_host_with_room(db_session, email="pv-route-owner@test.com")
        r = client.post(
            f"/api/users/hosting/rooms/{room.id}/property-verifications",
            data={"evidence_ref": "deed.pdf"},
            files={"file": ("deed.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 410, r.text

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


class TestRegexAutoVerify:
    """No OCR: details are grabbed with regex (document_regex.py) from the PDF
    text layer + typed evidence_ref, stored on the row, and matched against
    the host's name and the property address. Uploads auto-verify after
    AUTO_VERIFY_DELAY_SECONDS unless they're a duplicate or clearly mismatch."""

    @pytest.fixture(autouse=True)
    def _system_admin(self, db_session: Session):
        from app.core.config import settings

        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        db_session.commit()

    @staticmethod
    def _age(db: Session, record: PropertyVerification) -> None:
        record.created_at = datetime.now(timezone.utc) - timedelta(seconds=crud.AUTO_VERIFY_DELAY_SECONDS + 1)
        db.commit()

    def test_upload_waits_then_auto_verifies(self, db_session: Session):
        user, room = _make_host_with_room(db_session, email="pv-auto@test.com")
        record = _declare(db_session, user, room, evidence_ref="Title deed\nProperty Address: 1 Verify Way, Bengaluru")
        assert record.status == "pending"
        assert record.verifier_notes == crud.AUTO_VERIFY_PENDING_NOTE

        crud.list_property_verifications_for_room(db_session, room.id)
        db_session.refresh(record)
        assert record.status == "pending"  # not due yet

        self._age(db_session, record)
        crud.list_property_verifications_for_room(db_session, room.id)
        db_session.refresh(record)
        assert record.status == "verified"
        assert record.expires_at is not None

    def test_details_are_extracted_and_matched(self, db_session: Session):
        user, room = _make_host_with_room(db_session, email="pv-match@test.com")
        record = _declare(
            db_session, user, room,
            evidence_ref=f"Deed No: BLR/2024/991\nOwner Name: {user.full_name}\nProperty Address: 1 Verify Way, Bengaluru",
        )
        assert record.extracted_owner_name == user.full_name
        assert record.extracted_address == "1 Verify Way, Bengaluru"
        assert record.extracted_document_number == "BLR/2024/991"
        assert record.name_matched is True
        assert record.address_matched is True
        assert record.verifier_notes == crud.AUTO_VERIFY_PENDING_NOTE

    def test_name_and_address_mismatch_goes_to_review(self, db_session: Session):
        user, room = _make_host_with_room(db_session, email="pv-mismatch@test.com")
        record = _declare(
            db_session, user, room,
            evidence_ref="Owner Name: Somebody Else\nProperty Address: 77 Unrelated Street, Chennai",
        )
        assert record.name_matched is False
        assert record.address_matched is False
        assert record.verifier_notes == crud.REVIEW_PENDING_NOTE

        self._age(db_session, record)
        crud.list_property_verifications_for_room(db_session, room.id)
        db_session.refresh(record)
        assert record.status == "pending"  # never auto-verified

    def test_duplicate_document_from_another_host_goes_to_review(self, db_session: Session):
        first_user, first_room = _make_host_with_room(db_session, email="pv-dup1@test.com")
        second_user, second_room = _make_host_with_room(db_session, email="pv-dup2@test.com")
        common = dict(
            evidence_ref="title deed", stored_filename="test-stored.pdf", original_filename="deed.pdf",
            content_type="application/pdf", file_size=len(_PDF_BYTES), sha256_hash="a" * 64,
        )
        crud.declare_property_verification(db_session, first_user, first_room, **common)
        duplicate = crud.declare_property_verification(db_session, second_user, second_room, **common)
        assert duplicate.verifier_notes == crud.REVIEW_PENDING_NOTE
