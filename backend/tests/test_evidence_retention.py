"""ZR-ENG-CLR-012 Section 28/AC-28/AC-29: "Documents are never stored
permanently by default... Scheduled deletion is auditable." Covers the
on-demand retention sweep (no scheduler exists in this stack -- see
services/evidence_retention.py's own docstring) and its idempotency."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.identity_uploads import resolve_identity_document_path
from app.crud.evidence_vault import register_evidence_artifact
from app.models.identity_verification import IdentityVerification
from app.models.market_policy import MarketPolicyPack
from app.models.party import Party
from app.services.evidence_retention import sweep_expired_evidence
from tests.conftest import _make_admin, auth_admin_cookie


def _policy_pack(db: Session, *, jurisdiction: str, retention_days: int) -> MarketPolicyPack:
    existing = db.scalar(select(MarketPolicyPack).where(MarketPolicyPack.jurisdiction_code == jurisdiction))
    if existing:
        existing.identity_evidence_retention_days = retention_days
        db.commit()
        db.refresh(existing)
        return existing
    pack = MarketPolicyPack(jurisdiction_code=jurisdiction, version=1, effective_from=date.today(), identity_evidence_retention_days=retention_days)
    db.add(pack)
    db.commit()
    db.refresh(pack)
    return pack


def _identity_record_with_artifact(db: Session, *, jurisdiction: str, stored_filename: str):
    party = Party(party_type="renter", status="active", jurisdiction=jurisdiction)
    db.add(party)
    db.flush()
    record = IdentityVerification(party_id=party.id, document_type="passport", status="verified")
    db.add(record)
    db.commit()

    path = resolve_identity_document_path(stored_filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake identity document bytes")

    artifact = register_evidence_artifact(
        db, related_entity_type="identity_verification", related_entity_id=str(record.id),
        stored_filename=stored_filename, sha256_hash="deadbeef",
    )
    return record, artifact, path


class TestEvidenceRetentionSweep:
    def test_artifact_past_retention_is_deleted(self, db_session: Session):
        _policy_pack(db_session, jurisdiction="England", retention_days=90)
        record, artifact, path = _identity_record_with_artifact(db_session, jurisdiction="England", stored_filename="retention-test-1.pdf")
        artifact.created_at = datetime.now(timezone.utc) - timedelta(days=91)
        db_session.commit()

        deleted = sweep_expired_evidence(db_session)
        assert [a.id for a in deleted] == [artifact.id]

        db_session.refresh(artifact)
        assert artifact.deleted_at is not None
        assert not path.is_file()

    def test_artifact_within_retention_is_not_deleted(self, db_session: Session):
        _policy_pack(db_session, jurisdiction="England", retention_days=90)
        record, artifact, path = _identity_record_with_artifact(db_session, jurisdiction="England", stored_filename="retention-test-2.pdf")

        deleted = sweep_expired_evidence(db_session)
        assert deleted == []
        assert path.is_file()

    def test_no_policy_pack_leaves_artifact_untouched(self, db_session: Session):
        record, artifact, path = _identity_record_with_artifact(db_session, jurisdiction="ZZ-NOWHERE", stored_filename="retention-test-3.pdf")
        artifact.created_at = datetime.now(timezone.utc) - timedelta(days=9999)
        db_session.commit()

        deleted = sweep_expired_evidence(db_session)
        assert deleted == []
        assert path.is_file()

    def test_sweep_is_idempotent(self, db_session: Session):
        _policy_pack(db_session, jurisdiction="England", retention_days=90)
        record, artifact, path = _identity_record_with_artifact(db_session, jurisdiction="England", stored_filename="retention-test-4.pdf")
        artifact.created_at = datetime.now(timezone.utc) - timedelta(days=91)
        db_session.commit()

        first = sweep_expired_evidence(db_session)
        assert len(first) == 1
        second = sweep_expired_evidence(db_session)
        assert second == []

    def test_sweep_route_requires_super_admin(self, client, db_session: Session):
        admin = _make_admin(db_session, email="evr-plain-01@test.com", role="admin")
        r = client.post("/api/verification/evidence-artifacts/sweep-retention", cookies=auth_admin_cookie(admin))
        assert r.status_code == 403, r.text

    def test_sweep_route_full_flow(self, client, db_session: Session):
        _policy_pack(db_session, jurisdiction="England", retention_days=90)
        record, artifact, path = _identity_record_with_artifact(db_session, jurisdiction="England", stored_filename="retention-test-5.pdf")
        artifact.created_at = datetime.now(timezone.utc) - timedelta(days=91)
        db_session.commit()

        super_admin = _make_admin(db_session, email="evr-super-01@test.com", role="super_admin")
        r = client.post("/api/verification/evidence-artifacts/sweep-retention", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert r.json()["deletedCount"] == 1


class TestSweepExpiredDisputeEvidence:
    """Section 12 gap: identity verification was previously the only
    evidence type with any retention_days concept -- dispute evidence had
    hold/redact/manual-delete but no time-bound expiry."""

    def _open_case_with_evidence(self, client, db_session: Session, *, suffix: str):
        from tests.conftest import auth_user_cookie
        from tests.test_disputes import _make_occupancy_with_parties

        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email=f"evrd-host-{suffix}@test.com", renter_email=f"evrd-renter-{suffix}@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        case_id = r.json()["id"]

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence",
            files={"file": ("receipt.pdf", b"%PDF-1.4 fake evidence", "application/pdf")},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        return case_id, r.json()["id"]

    def test_not_configured_leaves_evidence_untouched(self, client, db_session: Session):
        from app.services.evidence_retention import sweep_expired_dispute_evidence

        case_id, evidence_id = self._open_case_with_evidence(client, db_session, suffix="evrd1")
        deleted = sweep_expired_dispute_evidence(db_session)
        assert deleted == []

    def test_deletes_expired_evidence_once_configured(self, client, db_session: Session):
        from app.models.dispute_evidence import DisputeEvidenceItem
        from app.services.evidence_retention import sweep_expired_dispute_evidence

        _case_id, evidence_id = self._open_case_with_evidence(client, db_session, suffix="evrd2")
        _policy_pack_dispute(db_session, jurisdiction="England", retention_days=30)

        item = db_session.get(DisputeEvidenceItem, evidence_id)
        item.created_at = datetime.now(timezone.utc) - timedelta(days=31)
        db_session.commit()

        deleted = sweep_expired_dispute_evidence(db_session)
        assert len(deleted) == 1
        db_session.refresh(item)
        assert item.deleted_at is not None
        assert item.stored_filename is None

    def test_active_legal_hold_blocks_deletion(self, client, db_session: Session):
        from app.models.dispute_evidence import DisputeEvidenceItem
        from app.services.evidence_retention import sweep_expired_dispute_evidence

        _case_id, evidence_id = self._open_case_with_evidence(client, db_session, suffix="evrd3")
        _policy_pack_dispute(db_session, jurisdiction="England", retention_days=30)

        item = db_session.get(DisputeEvidenceItem, evidence_id)
        item.created_at = datetime.now(timezone.utc) - timedelta(days=31)
        item.legal_hold = True
        db_session.commit()

        deleted = sweep_expired_dispute_evidence(db_session)
        assert deleted == []
        db_session.refresh(item)
        assert item.deleted_at is None


def _policy_pack_dispute(db: Session, *, jurisdiction: str, retention_days: int) -> MarketPolicyPack:
    existing = db.scalar(select(MarketPolicyPack).where(MarketPolicyPack.jurisdiction_code == jurisdiction))
    if existing:
        existing.dispute_evidence_retention_days = retention_days
        db.commit()
        db.refresh(existing)
        return existing
    pack = MarketPolicyPack(jurisdiction_code=jurisdiction, version=1, effective_from=date.today(), dispute_evidence_retention_days=retention_days)
    db.add(pack)
    db.commit()
    db.refresh(pack)
    return pack
