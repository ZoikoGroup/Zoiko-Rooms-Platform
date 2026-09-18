"""ZR-ENG-CLR-012 Section 8/24: verifying an identity document must issue a
scoped VerificationCredential(requirement_code="IDENTITY"), not just flip
IdentityVerification.status -- the same doctrine already covered for
OCCUPANCY_ELIGIBILITY in test_occupancy_eligibility_verification.py."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.crud.identity_verification import get_valid_identity_credential, verify_identity_verification
from app.models.identity_verification import IdentityVerification
from app.models.party import Party
from tests.conftest import _make_admin


def _make_pending(db: Session) -> IdentityVerification:
    party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()
    record = IdentityVerification(party_id=party.id, document_type="passport", status="pending")
    db.add(record)
    db.commit()
    return record


class TestIdentityCredentialIssuance:
    def test_verify_issues_a_valid_identity_credential(self, db_session: Session):
        record = _make_pending(db_session)
        super_admin = _make_admin(db_session, email="idc-super-01@test.com", role="super_admin")

        assert get_valid_identity_credential(db_session, record.party_id) is None

        verify_identity_verification(db_session, record, super_admin)

        credential = get_valid_identity_credential(db_session, record.party_id)
        assert credential is not None
        assert credential.requirement_code == "IDENTITY"
        assert credential.status == "VALID"
        assert credential.source_identity_verification_id == record.id
        assert credential.expires_at == record.expires_at

    def test_pending_record_has_no_credential(self, db_session: Session):
        record = _make_pending(db_session)
        assert get_valid_identity_credential(db_session, record.party_id) is None
