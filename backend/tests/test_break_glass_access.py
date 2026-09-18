"""ZR-ENG-CLR-012 AC-26/AC-27: 'Support agents cannot browse raw identity
documents by default' + 'Break-glass sensitive-data access is time-limited,
reason-coded and audited.' Covers: a plain admin is blocked by default, a
break-glass grant requires a reason, unblocks access for its duration, and
super admins never need one at all."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.crud.break_glass_access import grant_break_glass_access, has_valid_break_glass_access, revoke_break_glass_access
from app.models.identity_verification import IdentityVerification
from app.models.party import Party
from tests.conftest import _make_admin, auth_admin_cookie

_PDF_BYTES = b"%PDF-1.4 break glass test document"


def _verified_record_with_document(db: Session) -> IdentityVerification:
    party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()
    record = IdentityVerification(party_id=party.id, document_type="passport", status="verified")
    db.add(record)
    db.commit()

    from app.core.identity_uploads import resolve_identity_document_path
    stored_filename = f"break-glass-test-{record.id}.pdf"
    path = resolve_identity_document_path(stored_filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_PDF_BYTES)
    record.document_file_path = stored_filename
    record.document_file_content_type = "application/pdf"
    db.commit()
    return record


class TestPlainAdminBlockedByDefault:
    def test_plain_admin_cannot_download_without_a_grant(self, client, db_session: Session):
        record = _verified_record_with_document(db_session)
        plain_admin = _make_admin(db_session, email="bg-plain-01@test.com", role="admin")
        r = client.get(f"/api/identity-verifications/{record.id}/document", cookies=auth_admin_cookie(plain_admin))
        assert r.status_code == 403, r.text

    def test_super_admin_never_needs_a_grant(self, client, db_session: Session):
        record = _verified_record_with_document(db_session)
        super_admin = _make_admin(db_session, email="bg-super-01@test.com", role="super_admin")
        r = client.get(f"/api/identity-verifications/{record.id}/document", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text


class TestBreakGlassGrant:
    def test_grant_requires_a_reason(self, db_session: Session):
        record = _verified_record_with_document(db_session)
        admin = _make_admin(db_session, email="bg-admin-01@test.com", role="admin")
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            grant_break_glass_access(db_session, admin, related_entity_type="identity_verification", related_entity_id=str(record.id), reason="")

    def test_grant_unblocks_access_for_its_duration(self, client, db_session: Session):
        record = _verified_record_with_document(db_session)
        admin = _make_admin(db_session, email="bg-admin-02@test.com", role="admin")
        cookies = auth_admin_cookie(admin)

        r = client.post(
            f"/api/identity-verifications/{record.id}/break-glass-access",
            json={"reason": "Active dispute, support ticket #4821"}, cookies=cookies,
        )
        assert r.status_code == 201, r.text
        assert r.json()["expiresAt"] is not None

        r = client.get(f"/api/identity-verifications/{record.id}/document", cookies=cookies)
        assert r.status_code == 200, r.text

    def test_expired_grant_no_longer_grants_access(self, db_session: Session):
        record = _verified_record_with_document(db_session)
        admin = _make_admin(db_session, email="bg-admin-03@test.com", role="admin")

        grant = grant_break_glass_access(
            db_session, admin, related_entity_type="identity_verification", related_entity_id=str(record.id),
            reason="test", duration_minutes=15,
        )
        grant.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        assert has_valid_break_glass_access(db_session, admin.id, "identity_verification", str(record.id)) is False

    def test_revoked_grant_no_longer_grants_access(self, db_session: Session):
        record = _verified_record_with_document(db_session)
        admin = _make_admin(db_session, email="bg-admin-04@test.com", role="admin")

        grant = grant_break_glass_access(
            db_session, admin, related_entity_type="identity_verification", related_entity_id=str(record.id), reason="test",
        )
        assert has_valid_break_glass_access(db_session, admin.id, "identity_verification", str(record.id)) is True
        revoke_break_glass_access(db_session, grant)
        assert has_valid_break_glass_access(db_session, admin.id, "identity_verification", str(record.id)) is False

    def test_grant_is_scoped_to_the_specific_record(self, db_session: Session):
        record1 = _verified_record_with_document(db_session)
        record2 = _verified_record_with_document(db_session)
        admin = _make_admin(db_session, email="bg-admin-05@test.com", role="admin")

        grant_break_glass_access(
            db_session, admin, related_entity_type="identity_verification", related_entity_id=str(record1.id), reason="test",
        )
        assert has_valid_break_glass_access(db_session, admin.id, "identity_verification", str(record1.id)) is True
        assert has_valid_break_glass_access(db_session, admin.id, "identity_verification", str(record2.id)) is False
