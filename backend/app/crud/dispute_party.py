"""ZR-ENG-CLR-010 Section 23/25: dispute_party representation/audit
tracking. See app/models/dispute_party.py's own docstring for why this is
additive, not a replacement for crud/disputes.py's existing case-access
checks."""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin_user import AdminUser
from app.models.dispute import DisputeResolutionCase
from app.models.dispute_party import DISPUTE_PARTY_REPRESENTATION_TYPES, DisputeParty
from app.models.guest import Guest
from app.models.party import Party


def add_representative(
    db: Session,
    case: DisputeResolutionCase,
    admin: AdminUser,
    *,
    represents: str,
    representation_type: str,
    authority_evidence_ref: str,
    guest_id: str | None = None,
    party_id: int | None = None,
) -> DisputeParty:
    if represents not in ("RENTER", "HOST"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "represents must be RENTER or HOST")
    if representation_type not in DISPUTE_PARTY_REPRESENTATION_TYPES or representation_type == "SELF":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"representation_type must be one of {DISPUTE_PARTY_REPRESENTATION_TYPES[1:]}")
    if (guest_id is None) == (party_id is None):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A representative must be exactly one of a guest or a party")
    if not authority_evidence_ref.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "authority_evidence_ref is required to add a representative")
    if guest_id is not None and not db.get(Guest, guest_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Guest not found")
    if party_id is not None and not db.get(Party, party_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Party not found")

    dispute_party = DisputeParty(
        case_id=case.id,
        party_role="REPRESENTATIVE",
        guest_id=guest_id,
        party_id=party_id,
        represents=represents,
        representation_type=representation_type,
        authority_verified_at=datetime.now(timezone.utc),
        authority_evidence_ref=authority_evidence_ref,
        added_by_admin_id=admin.id,
    )
    db.add(dispute_party)
    db.commit()
    db.refresh(dispute_party)
    return dispute_party


def get_party_or_404(db: Session, dispute_party_id: int) -> DisputeParty:
    dispute_party = db.get(DisputeParty, dispute_party_id)
    if not dispute_party:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispute party record not found")
    return dispute_party


def list_parties_for_case(db: Session, case: DisputeResolutionCase) -> list[DisputeParty]:
    query = select(DisputeParty).where(DisputeParty.case_id == case.id).order_by(DisputeParty.added_at.asc())
    return list(db.scalars(query))


def set_communication_restrictions(db: Session, dispute_party: DisputeParty, admin: AdminUser, *, restrictions: str) -> DisputeParty:
    dispute_party.communication_restrictions = restrictions
    db.commit()
    db.refresh(dispute_party)
    return dispute_party


def communication_restrictions_for(db: Session, case: DisputeResolutionCase, *, guest: Guest | None = None, party_id: int | None = None) -> str:
    """Used by crud/dispute_message.py:assert_messaging_allowed -- returns
    the non-empty restriction text for this sender's own DisputeParty row
    on this case, or "" if none is set (including if no row exists at all,
    e.g. for a case opened before this phase shipped)."""
    query = select(DisputeParty).where(DisputeParty.case_id == case.id)
    if guest is not None:
        query = query.where(DisputeParty.guest_id == guest.id)
    elif party_id is not None:
        query = query.where(DisputeParty.party_id == party_id)
    else:
        return ""
    for row in db.scalars(query):
        if row.communication_restrictions.strip():
            return row.communication_restrictions
    return ""
