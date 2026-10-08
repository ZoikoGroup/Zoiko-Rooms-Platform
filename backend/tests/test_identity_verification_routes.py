"""Integration tests for /api/identity-verifications (admin side). The
document download route is covered by test_document_access.py; this covers
listing and that no admin route can create, approve, reject or ask for more
evidence -- identities are decided only by Veriff (ZR-IDV-ADR-001)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.crud.party import get_or_create_default_party
from app.models.identity_verification import IdentityVerification
from app.models.party import Party
from tests.conftest import _make_admin, auth_admin_cookie


class TestList:
    def test_plain_admin_sees_only_their_own_partys_records(self, client, db_session: Session):
        admin_a = _make_admin(db_session, email="idvr-a@test.com", role="admin")
        admin_b = _make_admin(db_session, email="idvr-b@test.com", role="admin")
        for admin, masked in ((admin_a, "••••AREF"), (admin_b, "••••BREF")):
            party = get_or_create_default_party(db_session, admin)
            db_session.add(IdentityVerification(party_id=party.id, document_type="passport", masked_document_number=masked,
                                                encrypted_reference="secret", status="pending", session_state="PROCESSING"))
        db_session.commit()

        r = client.get("/api/identity-verifications", cookies=auth_admin_cookie(admin_a))
        assert r.status_code == 200, r.text
        # Only ever the masked number (ZR-IDENTITY-001 Section 5.4).
        refs = {row["maskedDocumentNumber"] for row in r.json()}
        assert refs == {"••••AREF"}
        assert "encryptedReference" not in r.json()[0]

    def test_super_admin_can_filter_by_status(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="idvr-super@test.com", role="super_admin")
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        db_session.add(IdentityVerification(party_id=party.id, document_type="passport", status="pending"))
        db_session.add(IdentityVerification(party_id=party.id, document_type="passport", status="verified"))
        db_session.commit()

        r = client.get("/api/identity-verifications?status=verified", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert all(row["status"] == "verified" for row in r.json())
        assert len(r.json()) >= 1

    def test_unauthenticated_request_is_rejected(self, client):
        r = client.get("/api/identity-verifications")
        assert r.status_code == 401, r.text


class TestNoManualDecisions:
    @pytest.fixture()
    def record(self, db_session: Session) -> IdentityVerification:
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        record = IdentityVerification(party_id=party.id, document_type="passport", status="pending",
                                      session_state="PROCESSING")
        db_session.add(record)
        db_session.commit()
        return record

    @pytest.mark.parametrize("suffix", ["verify", "reject", "request-additional-evidence", "decision"])
    def test_a_super_admin_cannot_decide_an_identity(self, client, db_session: Session, record, suffix):
        super_admin = _make_admin(db_session, email=f"idvr-nodecide-{suffix}@test.com", role="super_admin")
        r = client.post(f"/api/identity-verifications/{record.id}/{suffix}", json={},
                        cookies=auth_admin_cookie(super_admin))
        assert r.status_code in (404, 405), r.text
        db_session.refresh(record)
        assert record.session_state == "PROCESSING"

    def test_an_admin_cannot_create_a_verification(self, client, db_session: Session):
        admin = _make_admin(db_session, email="idvr-nocreate@test.com", role="super_admin")
        r = client.post("/api/identity-verifications", json={"documentType": "passport", "encryptedReference": "X"},
                        cookies=auth_admin_cookie(admin))
        assert r.status_code == 405, r.text
