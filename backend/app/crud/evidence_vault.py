"""ZR-ENG-CLR-012 Section 18/AC-24: the Evidence Vault's provenance index.
Records a hash + storage pointer for every original evidence file at
ingest -- the actual bytes stay exactly where core/identity_uploads.py (or
an equivalent per-domain saver) already puts them; this only adds the
tamper-evident, queryable record the doc calls for.

Deliberately does NOT perform malware scanning, EXIF stripping, tamper
analysis or watermarking -- those need a real scanning/imaging provider
this stack doesn't have. scan_status stays "NOT_SCANNED" until one is
wired in; nothing here is allowed to claim a file is "CLEAN" that was
never actually scanned."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.evidence_artifact import EvidenceArtifact


def register_evidence_artifact(
    db: Session, *, related_entity_type: str, related_entity_id: str, stored_filename: str,
    sha256_hash: str, original_filename: str = "", content_type: str = "", file_size: int = 0,
    uploaded_by_admin_id: int | None = None, uploaded_by_user_id: int | None = None,
) -> EvidenceArtifact:
    artifact = EvidenceArtifact(
        related_entity_type=related_entity_type, related_entity_id=related_entity_id,
        stored_filename=stored_filename, sha256_hash=sha256_hash, original_filename=original_filename,
        content_type=content_type, file_size=file_size,
        uploaded_by_admin_id=uploaded_by_admin_id, uploaded_by_user_id=uploaded_by_user_id,
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact


def list_evidence_artifacts_for_entity(db: Session, related_entity_type: str, related_entity_id: str) -> list[EvidenceArtifact]:
    return list(
        db.scalars(
            select(EvidenceArtifact)
            .where(EvidenceArtifact.related_entity_type == related_entity_type, EvidenceArtifact.related_entity_id == related_entity_id)
            .order_by(EvidenceArtifact.created_at.desc())
        )
    )


def find_duplicate_by_hash(
    db: Session, sha256_hash: str, *, exclude_uploaded_by_user_id: int | None = None,
) -> EvidenceArtifact | None:
    """A prior upload with the exact same content already exists -- useful
    ops/fraud signal (Section 18: 'duplicate/fraud-pattern detection'),
    though this deliberately only flags the fact, not a verdict.

    exclude_uploaded_by_user_id scopes this to cross-account reuse: the same
    user re-uploading their own already-submitted document (e.g. resubmitting
    after being asked to, or just retrying) is normal and must not itself be
    treated as a fraud signal -- only a match against a *different* account's
    upload is. Passed as an exclusion (not a party-scoped lookup) so a match
    against another user still gets caught even when this user has never
    uploaded anything before."""
    query = select(EvidenceArtifact).where(EvidenceArtifact.sha256_hash == sha256_hash)
    if exclude_uploaded_by_user_id is not None:
        query = query.where(
            EvidenceArtifact.uploaded_by_user_id.is_distinct_from(exclude_uploaded_by_user_id)
        )
    return db.scalar(query)
