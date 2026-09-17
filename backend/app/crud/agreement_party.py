"""ZR-ENG-CLR-012 Section 15: 'Each person is its own Verification Subject
with role-scoped requirements and consent... Guarantor screening, identity
and agreement execution are separate from renter screening and may use
different evidence/retention rules.'

Adding a guarantor to an already-SIGNED agreement is a material change like
any other, so it goes through crud/agreement_amendments.py's real
REQUESTED -> ... -> EFFECTIVE pipeline (an ADDENDUM amendment) rather than a
silent direct insert -- this module supplies the two ADDENDUM-specific
pieces that pipeline doesn't otherwise know how to do: creating the
guarantor's own Party at approval time, and recording their consent.

Consent is intentionally tracked here on AgreementParty
(consent_method/consent_evidence_ref/consented_at), never by reusing
Agreement.signed_by_provider_at/signed_by_renter_at or _apply_signature:
that pipeline is hardcoded to exactly those two roles (ZR-ENG-CLR-004
Section 8.1/AC-10 -- 'Every profile today resolves to exactly ("provider",
"renter")'), and a guarantor has no login to self-serve-sign with in this
MVP. Wet-ink evidence (the same method already used elsewhere in this
codebase for an out-of-band signature) is what an admin attaches here on
the guarantor's behalf -- see record_guarantor_consent."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.crud.audit import log_audit_event
from app.models.admin_user import AdminUser
from app.models.agreement_amendment import AgreementAmendment
from app.models.agreement_party import AgreementParty
from app.models.leasing import Agreement
from app.models.party import Party


def create_guarantor_party_for_amendment(db: Session, agreement: Agreement, *, legal_name: str, contact_email: str) -> AgreementParty:
    """Called from crud/agreement_amendments.py:approve_amendment once an
    ADDENDUM amendment proposing a guarantor is approved -- never called
    directly from a route. Creates the guarantor's own Party (never reuses
    the renter's), unconsented (consented_at is None) until an admin
    records their evidence via record_guarantor_consent below."""
    guarantor_party = Party(
        party_type="guarantor", status="active",
        jurisdiction=agreement.offer.listing.market_release.jurisdiction if agreement.offer.listing.market_release else "IN",
    )
    db.add(guarantor_party)
    db.flush()

    agreement_party = AgreementParty(
        agreement_id=agreement.id, role="guarantor", party_type="individual",
        legal_name=legal_name.strip(), contact_email=contact_email.strip(), party_id=guarantor_party.id,
    )
    db.add(agreement_party)
    db.flush()
    return agreement_party


def get_agreement_party_or_404(db: Session, agreement: Agreement, agreement_party_id: int) -> AgreementParty:
    agreement_party = db.get(AgreementParty, agreement_party_id)
    if not agreement_party or agreement_party.agreement_id != agreement.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agreement party not found")
    return agreement_party


def record_guarantor_consent(
    db: Session, agreement_party: AgreementParty, admin: AdminUser, *, evidence_ref: str, method: str = "WET_INK",
) -> AgreementParty:
    if agreement_party.role != "guarantor":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Consent can only be recorded for a guarantor agreement party")
    if agreement_party.consented_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Consent has already been recorded for this guarantor")
    if not evidence_ref.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Evidence reference is required to record consent")

    agreement_party.consent_method = method
    agreement_party.consent_evidence_ref = evidence_ref.strip()
    agreement_party.consented_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(agreement_party)

    log_audit_event(
        db, admin, "agreement_party.guarantor_consent", "agreement_party", str(agreement_party.id),
        reason=f"method={method}",
    )
    db.commit()
    return agreement_party
