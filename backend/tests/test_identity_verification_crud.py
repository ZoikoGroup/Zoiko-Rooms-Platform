"""Unit tests for app/crud/identity_verification.py: party-scoped listing and
the verified-identity lookup other flows (listing publish, application
submission) gate on. Identities are decided only by Veriff (ZR-IDV-ADR-001):
this module has no create / approve / reject / request-evidence functions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.crud import identity_verification as crud
from app.crud.party import get_or_create_default_party
from app.models.identity_verification import IdentityVerification
from app.models.party import Party
from tests.conftest import _make_admin, _make_user


def _record_for(db: Session, admin, masked: str) -> IdentityVerification:
    party = get_or_create_default_party(db, admin)
    record = IdentityVerification(party_id=party.id, document_type="passport", masked_document_number=masked,
                                  status="pending", session_state="PROCESSING")
    db.add(record)
    db.commit()
    return record


class TestNoManualDecisions:
    def test_the_manual_review_functions_are_gone(self):
        for name in ("submit_identity_verification", "verify_identity_verification", "reject_identity_verification",
                     "request_additional_evidence", "submit_identity_verification_for_user"):
            assert not hasattr(crud, name), name


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


class TestListing:
    def test_list_user_identity_verifications_is_empty_without_a_party(self, db_session: Session):
        user = _make_user(db_session, email="idv-list1@test.com")
        db_session.commit()
        assert crud.list_user_identity_verifications(db_session, user) == []

    def test_plain_admin_only_sees_their_own_partys_verifications(self, db_session: Session):
        admin_a = _make_admin(db_session, email="idv-list-a@test.com", role="admin")
        admin_b = _make_admin(db_session, email="idv-list-b@test.com", role="admin")
        _record_for(db_session, admin_a, "••••A")
        _record_for(db_session, admin_b, "••••B")

        results_a = crud.list_identity_verifications(db_session, admin_a)
        assert len(results_a) == 1
        assert results_a[0].masked_document_number == "••••A"

    def test_super_admin_sees_every_partys_verifications(self, db_session: Session):
        admin_a = _make_admin(db_session, email="idv-list-a2@test.com", role="admin")
        admin_b = _make_admin(db_session, email="idv-list-b2@test.com", role="admin")
        super_admin = _make_admin(db_session, email="idv-list-super@test.com", role="super_admin")
        _record_for(db_session, admin_a, "••••A2")
        _record_for(db_session, admin_b, "••••B2")

        results = crud.list_identity_verifications(db_session, super_admin)
        refs = {r.masked_document_number for r in results}
        assert {"••••A2", "••••B2"}.issubset(refs)
