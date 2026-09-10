"""ZR-ENG-CLR-004 Section 4.7/9.4/10.1/AC-18: the post-execution change
workflow -- the counterpart to crud/leasing.py:_invalidate_pending_agreement_version
(AC-06), which only ever fires *before* execution. Once an agreement is
SIGNED, any material change has to go through this REQUESTED -> ... ->
EFFECTIVE pipeline instead, producing a new immutable version linked back to
the one it amends rather than editing anything in place (AC-07 still
applies to the source version throughout)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.crud.party import assert_provider_access, party_id_for_listing
from app.models.admin_user import AdminUser
from app.models.agreement_amendment import AMENDMENT_TYPES, AgreementAmendment
from app.models.leasing import Agreement, AgreementVersion
from app.services.agreement_profile import resolve_agreement_profile

_PROPOSABLE_TERM_KEYS = ("monthlyRent", "depositAmount", "startDate", "termMonths")


def get_amendment_or_404(db: Session, agreement: Agreement, amendment_id: int) -> AgreementAmendment:
    amendment = db.get(AgreementAmendment, amendment_id)
    if not amendment or amendment.agreement_id != agreement.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Amendment not found")
    return amendment


def request_amendment(db: Session, agreement: Agreement, admin: AdminUser, reason: str) -> AgreementAmendment:
    """REQUESTED. Only once the agreement is fully SIGNED -- before that,
    AC-06's own in-place reversioning (_invalidate_pending_agreement_version)
    is the correct path, not an amendment."""
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    if agreement.status != "SIGNED":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only a fully signed agreement can be amended -- an unsigned change should use offer terms instead",
        )
    source_version = agreement.versions[-1]
    if source_version.status != "EXECUTED_IMMUTABLE":
        raise HTTPException(status.HTTP_409_CONFLICT, "The current version is not yet executed")

    amendment = AgreementAmendment(
        agreement_id=agreement.id, source_version_id=source_version.id, reason=reason,
        requested_by_admin_id=admin.id, status="REQUESTED",
    )
    db.add(amendment)
    db.commit()
    db.refresh(amendment)
    return amendment


def classify_amendment(db: Session, amendment: AgreementAmendment, admin: AdminUser, amendment_type: str) -> AgreementAmendment:
    """REQUESTED -> CLASSIFIED."""
    assert_provider_access(db, admin, party_id_for_listing(amendment.agreement.offer.listing))
    if amendment.status != "REQUESTED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a REQUESTED amendment can be classified")
    if amendment_type not in AMENDMENT_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"amendmentType must be one of {list(AMENDMENT_TYPES)}")

    amendment.amendment_type = amendment_type
    amendment.status = "CLASSIFIED"
    amendment.classified_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(amendment)
    return amendment


def propose_terms(db: Session, amendment: AgreementAmendment, admin: AdminUser, proposed_terms: dict) -> AgreementAmendment:
    """CLASSIFIED -> TERMS_PROPOSED -> APPROVALS_PENDING. Both transitions
    happen in this one call -- see models/agreement_amendment.py's own
    docstring on why that's not a partial implementation: proposing terms
    IS submitting for approval, there is no additional fact the spec
    describes in between."""
    assert_provider_access(db, admin, party_id_for_listing(amendment.agreement.offer.listing))
    if amendment.status != "CLASSIFIED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a CLASSIFIED amendment can have terms proposed")

    invalid_keys = sorted(set(proposed_terms) - set(_PROPOSABLE_TERM_KEYS))
    if invalid_keys:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown proposed term field(s): {invalid_keys}")
    if not proposed_terms:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one proposed term change is required")

    now = datetime.now(timezone.utc)
    amendment.proposed_terms = proposed_terms
    amendment.status = "APPROVALS_PENDING"
    amendment.terms_proposed_at = now
    amendment.approvals_pending_at = now
    db.commit()
    db.refresh(amendment)
    return amendment


def _merged_snapshot(source_snapshot: dict, proposed_terms: dict) -> dict:
    """Copies the source version's snapshot unchanged except for the
    specific commercial fields the amendment actually proposes -- premises,
    parties, clause_ids, required_signers etc. all carry over untouched."""
    snapshot = dict(source_snapshot)
    if "monthlyRent" in proposed_terms:
        snapshot["monthly_rent"] = float(proposed_terms["monthlyRent"])
    if "depositAmount" in proposed_terms:
        snapshot["deposit_amount"] = float(proposed_terms["depositAmount"])
    if "startDate" in proposed_terms:
        snapshot["start_date"] = proposed_terms["startDate"]
    if "termMonths" in proposed_terms:
        snapshot["term_months"] = int(proposed_terms["termMonths"])
    return snapshot


def approve_amendment(db: Session, amendment: AgreementAmendment, admin: AdminUser, correlation_id: str = "") -> AgreementAmendment:
    """APPROVALS_PENDING -> GENERATED -> EXECUTION_PENDING. The actual
    version-generation step: builds a new AgreementVersion off the source
    version's snapshot with the proposed overrides applied, resets the
    agreement's signatures, and opens fresh signature_requests for it --
    exactly the AC-06 reversion mechanics, just entered from an executed
    agreement instead of a pending one, and evidenced by this Amendment row
    instead of being implicit."""
    from app.crud.leasing import _populate_version_detail_rows, create_signature_requests

    assert_provider_access(db, admin, party_id_for_listing(amendment.agreement.offer.listing))
    if amendment.status != "APPROVALS_PENDING":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an amendment awaiting approval can be approved")

    agreement = amendment.agreement
    source_version = db.get(AgreementVersion, amendment.source_version_id)
    offer = agreement.offer
    listing = offer.listing

    profile = resolve_agreement_profile(db, listing, listing.room)
    if profile is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No approved agreement profile for this listing's jurisdiction -- routed to manual review",
        )

    new_snapshot = _merged_snapshot(source_version.snapshot, amendment.proposed_terms)
    # AC-08/AC-25: re-stamp against whatever clause versions/market-pack tag
    # are currently effective, same as any other version generation -- an
    # amendment is a fresh generation event, not a copy of stale metadata.
    new_snapshot["clause_versions"] = {
        cid: v for cid, v in profile.clause_versions.items() if cid in new_snapshot.get("clause_ids", [])
    }
    new_snapshot["market_pack_version"] = profile.market_pack_version

    new_version = AgreementVersion(
        agreement_id=agreement.id, version_no=source_version.version_no + 1, status="WORKING",
        snapshot=new_snapshot,
    )
    db.add(new_version)
    db.flush()

    from datetime import date as date_
    from app.models.leasing import OfferTerms
    fake_terms = OfferTerms(
        offer_id=offer.id, version=0,
        monthly_rent=new_snapshot["monthly_rent"], deposit_amount=new_snapshot["deposit_amount"],
        start_date=date_.fromisoformat(new_snapshot["start_date"]) if isinstance(new_snapshot["start_date"], str) else new_snapshot["start_date"],
        term_months=new_snapshot["term_months"],
    )
    _populate_version_detail_rows(db, new_version, offer, fake_terms)

    agreement.signed_by_provider_at = None
    agreement.signed_by_renter_at = None
    agreement.signature_ref = ""
    agreement.status = "AMENDMENT_PENDING"
    create_signature_requests(db, agreement, new_version)

    now = datetime.now(timezone.utc)
    amendment.resulting_version_id = new_version.id
    amendment.status = "EXECUTION_PENDING"
    amendment.generated_at = now
    amendment.execution_pending_at = now

    log_audit_event(
        db, admin, "agreement.amended", "agreement", str(agreement.id), correlation_id,
        reason=amendment.amendment_type or "", before_state=f"v{source_version.version_no}", after_state=f"v{new_version.version_no}",
    )
    emit_event(
        db, "agreement.amended", "agreement", str(agreement.id),
        {"amendmentId": amendment.id, "resultingVersionNo": new_version.version_no}, correlation_id=correlation_id,
    )

    db.commit()
    db.refresh(amendment)
    return amendment


def list_amendments(db: Session, agreement: Agreement) -> list[AgreementAmendment]:
    return list(agreement.amendments)
