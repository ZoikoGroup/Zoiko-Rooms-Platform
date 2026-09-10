"""Unit tests for app/crud/identity_verification.py (previously 24% covered --
the document view/download path is already covered by test_document_access.py;
this covers the rest of the workflow: submit, verify, reject, party-scoped
listing, additional-evidence, and the verified-identity lookup other flows
(listing publish, application submission) gate on."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import identity_verification as crud
from app.crud.party import get_or_create_default_party
from app.models.identity_verification import IdentityVerification
from app.models.notification import Notification
from app.models.party import Party
from app.schemas.marketplace import IdentityVerificationCreate
from tests.conftest import _make_admin, _make_user


class TestSubmitIdentityVerificationAsAdmin:
    def test_rejects_an_invalid_document_type(self, db_session: Session):
        admin = _make_admin(db_session, email="idv-admin1@test.com", role="admin")
        with pytest.raises(HTTPException) as exc:
            crud.submit_identity_verification(
                db_session, admin, IdentityVerificationCreate(document_type="not-a-real-type", encrypted_reference="123")
            )
        assert exc.value.status_code == 400

    def test_requires_an_encrypted_reference(self, db_session: Session):
        admin = _make_admin(db_session, email="idv-admin2@test.com", role="admin")
        with pytest.raises(HTTPException) as exc:
            crud.submit_identity_verification(
                db_session, admin, IdentityVerificationCreate(document_type="passport", encrypted_reference="")
            )
        assert exc.value.status_code == 400

    def test_uses_the_admins_own_default_party_when_none_given(self, db_session: Session):
        admin = _make_admin(db_session, email="idv-admin3@test.com", role="admin")
        record = crud.submit_identity_verification(
            db_session, admin, IdentityVerificationCreate(document_type="passport", encrypted_reference="ABC123")
        )
        own_party = get_or_create_default_party(db_session, admin)
        assert record.party_id == own_party.id
        assert record.status == "pending"
        assert record.document_category == "identity"

    def test_plain_admin_cannot_submit_for_another_party(self, db_session: Session):
        admin = _make_admin(db_session, email="idv-admin4@test.com", role="admin")
        other_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(other_party)
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            crud.submit_identity_verification(
                db_session, admin,
                IdentityVerificationCreate(party_id=other_party.id, document_type="passport", encrypted_reference="X"),
            )
        assert exc.value.status_code == 403

    def test_super_admin_can_submit_for_another_party(self, db_session: Session):
        super_admin = _make_admin(db_session, email="idv-super1@test.com", role="super_admin")
        other_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(other_party)
        db_session.commit()

        record = crud.submit_identity_verification(
            db_session, super_admin,
            IdentityVerificationCreate(party_id=other_party.id, document_type="passport", encrypted_reference="X"),
        )
        assert record.party_id == other_party.id

    def test_super_admin_gets_404_for_an_unknown_party(self, db_session: Session):
        super_admin = _make_admin(db_session, email="idv-super2@test.com", role="super_admin")
        with pytest.raises(HTTPException) as exc:
            crud.submit_identity_verification(
                db_session, super_admin,
                IdentityVerificationCreate(party_id=999999, document_type="passport", encrypted_reference="X"),
            )
        assert exc.value.status_code == 404


class TestVerifyAndRejectRequireSuperAdmin:
    def _make_pending_record(self, db: Session) -> tuple[IdentityVerification, Party]:
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db.add(party)
        db.commit()
        record = IdentityVerification(party_id=party.id, document_type="passport", status="pending")
        db.add(record)
        db.commit()
        return record, party

    def test_verify_rejects_a_plain_admin(self, db_session: Session):
        record, _party = self._make_pending_record(db_session)
        plain_admin = _make_admin(db_session, email="idv-verify-plain@test.com", role="admin")
        with pytest.raises(HTTPException) as exc:
            crud.verify_identity_verification(db_session, record, plain_admin)
        assert exc.value.status_code == 403
        assert record.status == "pending"

    def test_verify_sets_status_and_expiry_and_notifies_the_linked_user(self, db_session: Session):
        record, party = self._make_pending_record(db_session)
        user = _make_user(db_session, email="idv-linked-user@test.com")
        user.party_id = party.id
        db_session.commit()

        super_admin = _make_admin(db_session, email="idv-verify-super@test.com", role="super_admin")
        before = datetime.now(timezone.utc)
        updated = crud.verify_identity_verification(db_session, record, super_admin)

        assert updated.status == "verified"
        assert updated.verifier_admin_id == super_admin.id
        assert updated.verified_at is not None
        assert updated.expires_at is not None
        assert updated.expires_at - before > timedelta(days=360)

        notification = db_session.query(Notification).filter(
            Notification.recipient_user_id == user.id,
            Notification.notification_type == "identity_verification.approved",
        ).one_or_none()
        assert notification is not None

    def test_reject_rejects_a_plain_admin(self, db_session: Session):
        record, _party = self._make_pending_record(db_session)
        plain_admin = _make_admin(db_session, email="idv-reject-plain@test.com", role="admin")
        with pytest.raises(HTTPException) as exc:
            crud.reject_identity_verification(db_session, record, plain_admin)
        assert exc.value.status_code == 403

    def test_reject_sets_status_and_notes(self, db_session: Session):
        record, _party = self._make_pending_record(db_session)
        super_admin = _make_admin(db_session, email="idv-reject-super@test.com", role="super_admin")
        updated = crud.reject_identity_verification(db_session, record, super_admin, "Blurry photo")
        assert updated.status == "rejected"
        assert updated.verifier_notes == "Blurry photo"
        assert updated.verifier_admin_id == super_admin.id

    def test_request_additional_evidence_requires_super_admin(self, db_session: Session):
        record, _party = self._make_pending_record(db_session)
        plain_admin = _make_admin(db_session, email="idv-evidence-plain@test.com", role="admin")
        with pytest.raises(HTTPException) as exc:
            crud.request_additional_evidence(db_session, record, plain_admin)
        assert exc.value.status_code == 403

    def test_request_additional_evidence_sets_status(self, db_session: Session):
        record, _party = self._make_pending_record(db_session)
        super_admin = _make_admin(db_session, email="idv-evidence-super@test.com", role="super_admin")
        updated = crud.request_additional_evidence(db_session, record, super_admin, "Please resend")
        assert updated.status == "additional_evidence_required"


class TestGetVerifiedIdentityForParty:
    def test_returns_none_when_no_verification_exists(self, db_session: Session):
        assert crud.get_verified_identity_for_party(db_session, 999999) is None

    def test_returns_none_for_a_pending_verification(self, db_session: Session):
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        db_session.add(IdentityVerification(party_id=party.id, document_type="passport", status="pending"))
        db_session.commit()
        assert crud.get_verified_identity_for_party(db_session, party.id) is None

    def test_returns_none_for_an_expired_verification(self, db_session: Session):
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        db_session.add(
            IdentityVerification(
                party_id=party.id, document_type="passport", status="verified",
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
        )
        db_session.commit()
        assert crud.get_verified_identity_for_party(db_session, party.id) is None

    def test_returns_a_currently_valid_verification(self, db_session: Session):
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.commit()
        record = IdentityVerification(
            party_id=party.id, document_type="passport", status="verified",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        db_session.add(record)
        db_session.commit()
        found = crud.get_verified_identity_for_party(db_session, party.id)
        assert found is not None
        assert found.id == record.id


class TestSubmitIdentityVerificationForUser:
    def test_rejects_an_invalid_document_type(self, db_session: Session):
        user = _make_user(db_session, email="idv-user1@test.com")
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        user.party_id = party.id
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            crud.submit_identity_verification_for_user(
                db_session, user, document_type="not-real", document_number="", custom_document_name="",
                stored_filename="f.pdf", original_filename="f.pdf", content_type="application/pdf", file_size=10,
            )
        assert exc.value.status_code == 400

    def test_other_document_type_requires_a_custom_name(self, db_session: Session):
        user = _make_user(db_session, email="idv-user2@test.com")
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        user.party_id = party.id
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            crud.submit_identity_verification_for_user(
                db_session, user, document_type="other", document_number="", custom_document_name="   ",
                stored_filename="f.pdf", original_filename="f.pdf", content_type="application/pdf", file_size=10,
            )
        assert exc.value.status_code == 400

    def test_user_with_no_party_is_rejected(self, db_session: Session):
        user = _make_user(db_session, email="idv-user3@test.com")
        db_session.commit()
        assert user.party_id is None

        with pytest.raises(HTTPException) as exc:
            crud.submit_identity_verification_for_user(
                db_session, user, document_type="passport", document_number="X", custom_document_name="",
                stored_filename="f.pdf", original_filename="f.pdf", content_type="application/pdf", file_size=10,
            )
        assert exc.value.status_code == 409

    def test_success_creates_a_pending_record_and_notifies_super_admins(self, db_session: Session):
        super_admin = _make_admin(db_session, email="idv-notify-super@test.com", role="super_admin")
        user = _make_user(db_session, email="idv-user4@test.com")
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        user.party_id = party.id
        db_session.commit()

        record = crud.submit_identity_verification_for_user(
            db_session, user, document_type="passport", document_number="P123", custom_document_name="",
            stored_filename="stored.pdf", original_filename="my-passport.pdf",
            content_type="application/pdf", file_size=1234,
        )
        assert record.status == "pending"
        assert record.party_id == party.id
        assert record.document_file_path == "stored.pdf"
        assert record.document_file_original_name == "my-passport.pdf"

        notification = db_session.query(Notification).filter(
            Notification.recipient_admin_id == super_admin.id,
            Notification.notification_type == "identity_verification.submitted",
        ).one_or_none()
        assert notification is not None


class TestListing:
    def test_list_user_identity_verifications_is_empty_without_a_party(self, db_session: Session):
        user = _make_user(db_session, email="idv-list1@test.com")
        db_session.commit()
        assert crud.list_user_identity_verifications(db_session, user) == []

    def test_plain_admin_only_sees_their_own_partys_verifications(self, db_session: Session):
        admin_a = _make_admin(db_session, email="idv-list-a@test.com", role="admin")
        admin_b = _make_admin(db_session, email="idv-list-b@test.com", role="admin")
        crud.submit_identity_verification(
            db_session, admin_a, IdentityVerificationCreate(document_type="passport", encrypted_reference="A")
        )
        crud.submit_identity_verification(
            db_session, admin_b, IdentityVerificationCreate(document_type="passport", encrypted_reference="B")
        )

        results_a = crud.list_identity_verifications(db_session, admin_a)
        assert len(results_a) == 1
        assert results_a[0].encrypted_reference == "A"

    def test_super_admin_sees_every_partys_verifications(self, db_session: Session):
        admin_a = _make_admin(db_session, email="idv-list-a2@test.com", role="admin")
        admin_b = _make_admin(db_session, email="idv-list-b2@test.com", role="admin")
        super_admin = _make_admin(db_session, email="idv-list-super@test.com", role="super_admin")
        crud.submit_identity_verification(
            db_session, admin_a, IdentityVerificationCreate(document_type="passport", encrypted_reference="A2")
        )
        crud.submit_identity_verification(
            db_session, admin_b, IdentityVerificationCreate(document_type="passport", encrypted_reference="B2")
        )

        results = crud.list_identity_verifications(db_session, super_admin)
        refs = {r.encrypted_reference for r in results}
        assert {"A2", "B2"}.issubset(refs)
