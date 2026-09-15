"""ZR-ENG-CLR-012 Section 18/AC-24: "Hash evidence artifacts at ingest...
Raw identity/biometric evidence is stored in a restricted Evidence Vault,
not general profile/document storage." Covers the hash-at-ingest wiring on
the real identity-document upload path, and that scan_status stays honest
(NOT_SCANNED, never a fabricated CLEAN) since no live scanning provider is
wired in."""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from app.crud.evidence_vault import find_duplicate_by_hash, list_evidence_artifacts_for_entity, register_evidence_artifact
from app.models.party import Party
from tests.conftest import _make_user, auth_user_cookie

_PDF_BYTES = b"%PDF-1.4 fake identity document content for evidence vault test"


def _verified_user_with_party(db: Session, email: str):
    user = _make_user(db, email=email)
    party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()
    user.party_id = party.id
    db.commit()
    return user


class TestEvidenceVaultRegistration:
    def test_register_computes_no_side_effects_and_defaults_to_not_scanned(self, db_session: Session):
        artifact = register_evidence_artifact(
            db_session, related_entity_type="identity_verification", related_entity_id="1",
            stored_filename="abc123.pdf", sha256_hash=hashlib.sha256(_PDF_BYTES).hexdigest(),
            original_filename="passport.pdf", content_type="application/pdf", file_size=len(_PDF_BYTES),
        )
        assert artifact.scan_status == "NOT_SCANNED"
        assert artifact.sha256_hash == hashlib.sha256(_PDF_BYTES).hexdigest()

    def test_list_for_entity_is_scoped_to_that_entity_only(self, db_session: Session):
        register_evidence_artifact(
            db_session, related_entity_type="identity_verification", related_entity_id="10",
            stored_filename="a.pdf", sha256_hash="hash-a",
        )
        register_evidence_artifact(
            db_session, related_entity_type="identity_verification", related_entity_id="11",
            stored_filename="b.pdf", sha256_hash="hash-b",
        )
        results = list_evidence_artifacts_for_entity(db_session, "identity_verification", "10")
        assert len(results) == 1
        assert results[0].stored_filename == "a.pdf"

    def test_duplicate_hash_is_detectable(self, db_session: Session):
        h = hashlib.sha256(b"same content").hexdigest()
        register_evidence_artifact(
            db_session, related_entity_type="identity_verification", related_entity_id="20",
            stored_filename="first.pdf", sha256_hash=h,
        )
        assert find_duplicate_by_hash(db_session, h) is not None
        assert find_duplicate_by_hash(db_session, "no-such-hash") is None


class TestEvidenceVaultWiredIntoRealUpload:
    def test_submitting_identity_document_registers_an_evidence_artifact(self, client, db_session: Session):
        user = _verified_user_with_party(db_session, email="evv-user-01@test.com")
        r = client.post(
            "/api/users/identity-verifications",
            data={"document_type": "passport", "document_number": "P123456"},
            files={"file": ("passport.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 201, r.text
        verification_id = r.json()["id"]

        artifacts = list_evidence_artifacts_for_entity(db_session, "identity_verification", str(verification_id))
        assert len(artifacts) == 1
        assert artifacts[0].sha256_hash == hashlib.sha256(_PDF_BYTES).hexdigest()
        assert artifacts[0].scan_status == "NOT_SCANNED"
        assert artifacts[0].uploaded_by_user_id == user.id
