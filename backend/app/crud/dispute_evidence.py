from datetime import datetime, timezone

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dispute_evidence_uploads import delete_dispute_evidence_file, save_dispute_evidence_file
from app.models.admin_user import AdminUser
from app.models.dispute import DisputeResolutionCase, DisputeResolutionClaim
from app.models.dispute_evidence import (
    DISPUTE_EVIDENCE_DISCLOSURE_CLASSES,
    DISPUTE_EVIDENCE_PROVENANCE,
    DisputeEvidenceClaimLink,
    DisputeEvidenceItem,
)
from app.models.dispute_legal_hold import DisputeLegalHold
from app.models.guest import Guest
from app.services.dispute_rbac import PRIVILEGED_EVIDENCE_DISPUTE_ROLES
from app.services.dispute_state_machine import transition_evidence_verification

# Section 21: what a non-admin viewer (renter or host, on their own case)
# may ever see -- never INTERNAL_ONLY/PRIVILEGED_RESTRICTED/
# EXTERNAL_EXPORT_ELIGIBLE, regardless of who uploaded it (AC-18).
_PARTY_VISIBLE_DISCLOSURE_CLASSES = ("PARTY_VISIBLE",)
# Section 6 gap: the renter's own strictly wider set -- RENTER_VISIBLE_ONLY
# on top of everything a Host may see, never the reverse.
_RENTER_VISIBLE_DISCLOSURE_CLASSES = ("PARTY_VISIBLE", "RENTER_VISIBLE_ONLY")


def _admin_may_see_privileged(admin: AdminUser | None) -> bool:
    """QA-Q40: "ordinary Support cannot access restricted item" -- Section
    21's PRIVILEGED_RESTRICTED disclosure class previously let ANY
    authenticated admin bypass it entirely (assert_evidence_downloadable's
    old `if admin is not None: return`), which is exactly the RBAC hole
    Q40 names. Only super_admin or an admin actually specialized into
    TRUST_AND_SAFETY/LEGAL_COMPLIANCE (Section 25's own privileged-access
    roles) may see PRIVILEGED_RESTRICTED evidence; every other admin --
    including one with no dispute_role assigned yet -- is treated as
    ordinary Support for this one disclosure class. This is the one place
    in the dispute RBAC layer that is NOT opt-in-only (unlike
    assert_dispute_role's write-side gates): reading privileged material
    defaults to denied, not allowed, because Section 25 states the
    restriction as the default rule for Support, not as a narrowing that
    only takes effect once configured."""
    if admin is None:
        return False
    if admin.role == "super_admin":
        return True
    return admin.dispute_role in PRIVILEGED_EVIDENCE_DISPUTE_ROLES


async def upload_evidence(
    db: Session,
    case: DisputeResolutionCase,
    *,
    file: UploadFile | None = None,
    note_text: str = "",
    claim_ids: list[int] | None = None,
    guest: Guest | None = None,
    party_id: int | None = None,
    admin: AdminUser | None = None,
    provenance: str | None = None,
    disclosure_class: str | None = None,
    captured_at: datetime | None = None,
) -> DisputeEvidenceItem:
    uploader_count = sum(1 for u in (guest, party_id, admin) if u is not None)
    if uploader_count != 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Evidence must be uploaded by exactly one of a renter, a host or an admin")
    if file is None and not note_text.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provide a file or note_text -- evidence cannot be empty")

    stored_filename = original_filename = content_type = sha256_hash = ""
    size_bytes = 0
    if file is not None:
        stored_filename, original_filename, content_type, size_bytes, sha256_hash = await save_dispute_evidence_file(file)

    if admin is not None:
        resolved_provenance = provenance or "ADMIN_NOTE"
        resolved_disclosure = disclosure_class or "INTERNAL_ONLY"
    else:
        resolved_provenance = provenance or "USER_UPLOAD"
        resolved_disclosure = disclosure_class or "PARTY_VISIBLE"

    if resolved_provenance not in DISPUTE_EVIDENCE_PROVENANCE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown provenance '{resolved_provenance}'")
    if resolved_disclosure not in DISPUTE_EVIDENCE_DISCLOSURE_CLASSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown disclosure class '{resolved_disclosure}'")

    claims: list[DisputeResolutionClaim] = []
    for claim_id in claim_ids or []:
        claim = db.get(DisputeResolutionClaim, claim_id)
        if not claim or claim.case_id != case.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Claim {claim_id} does not belong to this case")
        claims.append(claim)

    evidence = DisputeEvidenceItem(
        case_id=case.id,
        provenance=resolved_provenance,
        uploaded_by_guest_id=guest.id if guest is not None else None,
        uploaded_by_party_id=party_id,
        uploaded_by_admin_id=admin.id if admin is not None else None,
        stored_filename=stored_filename or None,
        original_filename=original_filename,
        content_type=content_type,
        size_bytes=size_bytes,
        sha256_hash=sha256_hash,
        note_text=note_text,
        disclosure_class=resolved_disclosure,
        captured_at=captured_at,
    )
    db.add(evidence)
    db.flush()

    for claim in claims:
        db.add(DisputeEvidenceClaimLink(evidence_id=evidence.id, claim_id=claim.id))

    db.commit()
    db.refresh(evidence)
    return evidence


def get_evidence_or_404(db: Session, evidence_id: int) -> DisputeEvidenceItem:
    evidence = db.get(DisputeEvidenceItem, evidence_id)
    if not evidence:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence item not found")
    return evidence


def claim_ids_for_evidence(db: Session, evidence: DisputeEvidenceItem) -> list[int]:
    return list(db.scalars(select(DisputeEvidenceClaimLink.claim_id).where(DisputeEvidenceClaimLink.evidence_id == evidence.id)))


def list_evidence_for_case(
    db: Session, case: DisputeResolutionCase, *,
    viewer_is_admin: bool, viewer_admin: AdminUser | None = None, viewer_is_host: bool = False,
) -> list[DisputeEvidenceItem]:
    query = select(DisputeEvidenceItem).where(DisputeEvidenceItem.case_id == case.id).order_by(DisputeEvidenceItem.created_at.desc())
    if not viewer_is_admin:
        # Section 6 gap: a Host viewer gets the narrower, Host-safe set --
        # RENTER_VISIBLE_ONLY evidence (e.g. backing a sensitive/protected-
        # ground claim) never reaches this query for them, even though it's
        # visible to the renter on the exact same case.
        allowed = _PARTY_VISIBLE_DISCLOSURE_CLASSES if viewer_is_host else _RENTER_VISIBLE_DISCLOSURE_CLASSES
        query = query.where(DisputeEvidenceItem.disclosure_class.in_(allowed))
    elif not _admin_may_see_privileged(viewer_admin):
        # QA-Q40: an admin viewer sees everything an admin normally does
        # EXCEPT PRIVILEGED_RESTRICTED items, unless specialized into
        # TRUST_AND_SAFETY/LEGAL_COMPLIANCE (or super_admin).
        query = query.where(DisputeEvidenceItem.disclosure_class != "PRIVILEGED_RESTRICTED")
    return list(db.scalars(query))


def assert_evidence_downloadable(
    evidence: DisputeEvidenceItem, *, guest: Guest | None = None, party_id: int | None = None, admin: AdminUser | None = None,
) -> None:
    if admin is not None:
        if evidence.disclosure_class == "PRIVILEGED_RESTRICTED" and not _admin_may_see_privileged(admin):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This evidence item is restricted to Trust & Safety / Legal-Compliance")
        return
    # Section 6 gap: party_id (a Host, per this module's own call sites --
    # see api/routes/disputes.py's host_router) gets the narrower set; a
    # renter (guest) gets RENTER_VISIBLE_ONLY too.
    allowed = _PARTY_VISIBLE_DISCLOSURE_CLASSES if party_id is not None else _RENTER_VISIBLE_DISCLOSURE_CLASSES
    if evidence.disclosure_class not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This evidence item is not visible to you")
    if evidence.stored_filename is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This evidence item has no downloadable file")


async def create_redaction(db: Session, original: DisputeEvidenceItem, admin: AdminUser, *, file: UploadFile) -> DisputeEvidenceItem:
    """Section 21: 'Preserve original; create separate redacted derivative.
    Never overwrite the evidentiary original.' `original` is never mutated
    here -- the redaction is a brand new, independently disclosable row."""
    if original.deleted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot redact evidence that has already been deleted")
    stored_filename, original_filename, content_type, size_bytes, sha256_hash = await save_dispute_evidence_file(file)

    redaction = DisputeEvidenceItem(
        case_id=original.case_id,
        provenance="ADMIN_NOTE",
        uploaded_by_admin_id=admin.id,
        stored_filename=stored_filename,
        original_filename=original_filename,
        content_type=content_type,
        size_bytes=size_bytes,
        sha256_hash=sha256_hash,
        note_text=f"Redacted derivative of evidence #{original.id}",
        disclosure_class="PARTY_VISIBLE",
        redacted_of_evidence_id=original.id,
    )
    db.add(redaction)
    db.flush()

    for claim_id in claim_ids_for_evidence(db, original):
        db.add(DisputeEvidenceClaimLink(evidence_id=redaction.id, claim_id=claim_id))

    db.commit()
    db.refresh(redaction)
    return redaction


def _active_legal_hold(db: Session, evidence: DisputeEvidenceItem) -> DisputeLegalHold | None:
    return db.scalar(
        select(DisputeLegalHold).where(DisputeLegalHold.evidence_id == evidence.id, DisputeLegalHold.status == "ACTIVE")
    )


def list_legal_holds_for_case(db: Session, case: DisputeResolutionCase) -> list[DisputeLegalHold]:
    return list(
        db.scalars(select(DisputeLegalHold).where(DisputeLegalHold.case_id == case.id).order_by(DisputeLegalHold.placed_at))
    )


def set_legal_hold(db: Session, evidence: DisputeEvidenceItem, admin: AdminUser, hold: bool, *, reason: str = "") -> DisputeEvidenceItem:
    """Section 21: a first-class, auditable legal hold -- see
    models/dispute_legal_hold.py's own docstring for why this replaced a
    bare boolean flip. evidence.legal_hold stays a derived convenience
    column mirroring "is there currently an ACTIVE row for this item"."""
    existing_active = _active_legal_hold(db, evidence)
    if hold:
        if evidence.deleted_at is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Cannot place a legal hold on evidence that has already been deleted")
        if existing_active is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "This evidence item is already under an active legal hold")
        db.add(DisputeLegalHold(
            evidence_id=evidence.id, case_id=evidence.case_id, status="ACTIVE", reason=reason, placed_by_admin_id=admin.id,
        ))
        evidence.legal_hold = True
    else:
        if existing_active is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "This evidence item has no active legal hold to release")
        existing_active.status = "RELEASED"
        existing_active.released_by_admin_id = admin.id
        existing_active.released_at = datetime.now(timezone.utc)
        evidence.legal_hold = False
    db.commit()
    db.refresh(evidence)
    return evidence


def verify_evidence(db: Session, evidence: DisputeEvidenceItem, admin: AdminUser, *, verified: bool) -> DisputeEvidenceItem:
    if evidence.deleted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot verify evidence that has already been deleted")
    transition_evidence_verification(evidence, "VERIFIED" if verified else "UNVERIFIED")
    db.commit()
    db.refresh(evidence)
    return evidence


def archive_evidence(db: Session, evidence: DisputeEvidenceItem, admin: AdminUser) -> DisputeEvidenceItem:
    # Section 21: a legal hold is a preservation obligation -- archiving
    # must not be allowed to proceed over an active one.
    if evidence.legal_hold:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot archive evidence under an active legal hold -- release the hold first")
    if evidence.deleted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot archive evidence that has already been deleted")
    transition_evidence_verification(evidence, "ARCHIVED")
    db.commit()
    db.refresh(evidence)
    return evidence


def _assert_can_request_deletion(
    evidence: DisputeEvidenceItem, *, guest: Guest | None = None, party_id: int | None = None, admin: AdminUser | None = None,
) -> None:
    if admin is not None:
        return
    if guest is not None and evidence.uploaded_by_guest_id == guest.id:
        return
    if party_id is not None and evidence.uploaded_by_party_id == party_id:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only request deletion of evidence you uploaded")


def request_deletion(
    db: Session, evidence: DisputeEvidenceItem, *, guest: Guest | None = None, party_id: int | None = None, admin: AdminUser | None = None,
) -> DisputeEvidenceItem:
    """QA-Q16: a data-subject erasure request against dispute evidence --
    the first real consumer of legal_hold as an exemption anywhere in this
    codebase, which otherwise has no deletion flow of any kind. Only the
    original uploader (or any admin, mirroring verify/archive/legal-hold's
    own admin-can-action-anything convention) may request deletion of a
    given item.

    Granted immediately unless an active legal hold exempts it (Section
    21's preservation obligation beats an ordinary erasure request).
    deletion_requested_at is recorded either way -- a refusal is still a
    real, auditable outcome, not silence. Only the file bytes and other
    personal content are erased; the row itself (and its claim links)
    stay, exactly like an ARCHIVED evidence item -- so the case chronology
    and any redaction lineage pointing at this row are never broken by a
    granted erasure."""
    _assert_can_request_deletion(evidence, guest=guest, party_id=party_id, admin=admin)
    if evidence.deleted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This evidence item has already been deleted")

    evidence.deletion_requested_at = datetime.now(timezone.utc)
    if evidence.legal_hold:
        evidence.deletion_refused_reason = "Refused: this evidence item is under an active legal hold"
        db.commit()
        db.refresh(evidence)
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot delete evidence under an active legal hold")

    if evidence.stored_filename:
        delete_dispute_evidence_file(evidence.stored_filename)
    evidence.stored_filename = None
    evidence.original_filename = ""
    evidence.note_text = ""
    evidence.deletion_refused_reason = ""
    evidence.deleted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(evidence)
    return evidence
