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

from app.crud.agreement_legal_hold import get_active_legal_hold
from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.crud.party import assert_provider_access_any, party_id_for_listing
from app.crud.provider_actor import ProviderActor, admin_id, admin_or_none, audit_reason, user_id
from app.models.agreement_amendment import AMENDMENT_TYPES, AgreementAmendment
from app.models.leasing import Agreement, AgreementVersion
from app.services.agreement_profile import no_agreement_profile_message, resolve_agreement_profile

# Every step below takes the provider-side actor: an admin acting for the
# provider party, or the self-service host (crud/provider_actor.py).

_PROPOSABLE_TERM_KEYS = ("monthlyRent", "depositAmount", "startDate", "termMonths")


def get_amendment_or_404(db: Session, agreement: Agreement, amendment_id: int) -> AgreementAmendment:
    amendment = db.get(AgreementAmendment, amendment_id)
    if not amendment or amendment.agreement_id != agreement.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Amendment not found")
    return amendment


def request_amendment(db: Session, agreement: Agreement, admin: ProviderActor, reason: str) -> AgreementAmendment:
    """REQUESTED. Only once the agreement is fully SIGNED -- before that,
    AC-06's own in-place reversioning (_invalidate_pending_agreement_version)
    is the correct path, not an amendment."""
    assert_provider_access_any(db, admin, party_id_for_listing(agreement.offer.listing))
    if agreement.status != "SIGNED":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only a fully signed agreement can be amended -- an unsigned change should use offer terms instead",
        )
    source_version = agreement.versions[-1]
    if source_version.status != "EXECUTED_IMMUTABLE":
        raise HTTPException(status.HTTP_409_CONFLICT, "The current version is not yet executed")
    # Section 4 gap: a legal hold is a preservation obligation -- amending
    # must not be allowed to proceed over an active one (same shape as
    # dispute_evidence.py:archive_evidence refusing over an active hold).
    if get_active_legal_hold(db, agreement) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot amend an agreement under an active legal hold -- release the hold first",
        )

    amendment = AgreementAmendment(
        agreement_id=agreement.id, source_version_id=source_version.id, reason=reason,
        requested_by_admin_id=admin_id(admin), requested_by_user_id=user_id(admin), status="REQUESTED",
    )
    db.add(amendment)
    db.commit()
    db.refresh(amendment)
    return amendment


def classify_amendment(db: Session, amendment: AgreementAmendment, admin: ProviderActor, amendment_type: str) -> AgreementAmendment:
    """REQUESTED -> CLASSIFIED."""
    assert_provider_access_any(db, admin, party_id_for_listing(amendment.agreement.offer.listing))
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


def propose_terms(db: Session, amendment: AgreementAmendment, admin: ProviderActor, proposed_terms: dict) -> AgreementAmendment:
    """CLASSIFIED -> TERMS_PROPOSED -> APPROVALS_PENDING. Both transitions
    happen in this one call -- see models/agreement_amendment.py's own
    docstring on why that's not a partial implementation: proposing terms
    IS submitting for approval, there is no additional fact the spec
    describes in between."""
    assert_provider_access_any(db, admin, party_id_for_listing(amendment.agreement.offer.listing))
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


def propose_guarantor_addition(
    db: Session, amendment: AgreementAmendment, admin: ProviderActor, *, legal_name: str, contact_email: str,
) -> AgreementAmendment:
    """ZR-ENG-CLR-012 Section 15: the ADDENDUM-specific counterpart to
    propose_terms above -- a guarantor is a party-roster change, not a
    commercial-terms field, so it gets its own proposal slot
    (proposed_guarantor) rather than being force-fit into proposed_terms.
    CLASSIFIED -> APPROVALS_PENDING, same transition shape as propose_terms."""
    assert_provider_access_any(db, admin, party_id_for_listing(amendment.agreement.offer.listing))
    if amendment.status != "CLASSIFIED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a CLASSIFIED amendment can have a guarantor proposed")
    if amendment.amendment_type != "ADDENDUM":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A guarantor addition must be classified as ADDENDUM")
    if not legal_name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Guarantor legal name is required")

    now = datetime.now(timezone.utc)
    amendment.proposed_guarantor = {"legalName": legal_name.strip(), "contactEmail": contact_email.strip()}
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


def approve_amendment(db: Session, amendment: AgreementAmendment, admin: ProviderActor, correlation_id: str = "") -> AgreementAmendment:
    """APPROVALS_PENDING -> GENERATED -> EXECUTION_PENDING. The actual
    version-generation step: builds a new AgreementVersion off the source
    version's snapshot with the proposed overrides applied, resets the
    agreement's signatures, and opens fresh signature_requests for it --
    exactly the AC-06 reversion mechanics, just entered from an executed
    agreement instead of a pending one, and evidenced by this Amendment row
    instead of being implicit."""
    from app.crud.leasing import _populate_version_detail_rows, create_signature_requests

    assert_provider_access_any(db, admin, party_id_for_listing(amendment.agreement.offer.listing))
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
            no_agreement_profile_message(db, listing, listing.room),
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

    # Persist a real OfferTerms row (mirroring crud/leasing.py:add_offer_terms)
    # rather than an in-memory-only stand-in -- confirm_move_in reads
    # offer.terms[-1] to compute expected_end_date, so an amendment that
    # changes startDate/termMonths but never saves a new terms row would
    # leave that calculation silently working off the pre-amendment dates.
    from datetime import date as date_
    from app.models.leasing import OfferTerms
    next_terms_version = offer.current_version + 1
    new_terms = OfferTerms(
        offer_id=offer.id, version=next_terms_version,
        monthly_rent=new_snapshot["monthly_rent"], deposit_amount=new_snapshot["deposit_amount"],
        start_date=date_.fromisoformat(new_snapshot["start_date"]) if isinstance(new_snapshot["start_date"], str) else new_snapshot["start_date"],
        term_months=new_snapshot["term_months"],
    )
    db.add(new_terms)
    offer.current_version = next_terms_version
    db.flush()
    _populate_version_detail_rows(db, new_version, offer, new_terms)

    # ZR-ENG-CLR-012 Section 15: an ADDENDUM proposing a guarantor gets them
    # their own AgreementParty (and their own Party for identity/screening
    # checks) here, at approval time -- see crud/agreement_party.py's
    # module docstring for why their consent is tracked separately rather
    # than folded into the provider/renter re-signature below.
    if amendment.proposed_guarantor:
        from app.crud.agreement_party import create_guarantor_party_for_amendment

        create_guarantor_party_for_amendment(
            db, agreement, legal_name=amendment.proposed_guarantor.get("legalName", ""),
            contact_email=amendment.proposed_guarantor.get("contactEmail", ""),
        )

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
        db, admin_or_none(admin), "agreement.amended", "agreement", str(agreement.id), correlation_id,
        reason=audit_reason(admin, amendment.amendment_type or ""), before_state=f"v{source_version.version_no}", after_state=f"v{new_version.version_no}",
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


def propose_provider_amendment(
    db: Session, agreement: Agreement, actor: ProviderActor, *,
    monthly_rent: float | None = None, additional_term_months: int | None = None, reason: str = "",
) -> AgreementAmendment:
    """The provider's own extension / rent change on a signed agreement: the
    same four-step engine run the change-request approval path uses
    (request -> classify -> propose -> approve), producing a new version that
    both sides must sign. The renter's signature on that version is their
    consent -- until they sign, the executed version stays in force."""
    from app.crud import notification as notif_crud

    assert_provider_access_any(db, actor, party_id_for_listing(agreement.offer.listing))
    current = agreement.offer.terms[-1] if agreement.offer.terms else None
    if current is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This agreement has no terms to amend")

    proposed: dict = {}
    labels: list[str] = []
    if monthly_rent is not None:
        if monthly_rent <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Monthly rent must be greater than zero")
        if float(monthly_rent) == float(current.monthly_rent):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That's the current rent")
        proposed["monthlyRent"] = float(monthly_rent)
        labels.append("rent change")
    if additional_term_months:
        if additional_term_months < 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "An extension must add at least one month")
        proposed["termMonths"] = current.term_months + additional_term_months
        labels.append("extension")
    if not proposed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give a new rent or a number of months to extend by")

    label = " and ".join(labels)
    amendment = request_amendment(db, agreement, actor, reason=reason.strip() or f"Host-proposed {label}")
    amendment = classify_amendment(db, amendment, actor, "MATERIAL_CHANGE")
    amendment = propose_terms(db, amendment, actor, proposed)
    amendment = approve_amendment(db, amendment, actor)

    notif_crud.notify_user_by_guest(
        db, agreement.offer.guest,
        title=f"Your host proposed a {label}",
        message="Review the updated agreement and sign it if you agree. Your current agreement stays in force until you do.",
        notification_type="agreement.amendment_proposed",
        related_entity_type="agreement", related_entity_id=f"{agreement.id}:{amendment.id}",
    )
    db.commit()
    return amendment
