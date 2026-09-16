"""ZR-ENG-CLR-012 Section 19: Renter Verification Center -- "Primary renter
surface showing only requirements relevant to the current booking/application
and explaining why each is needed." This is the read-only status summary
half of that: identity + occupancy-eligibility credential state for the
signed-in renter, scoped to their own party (never another renter's, per
Section 15's "joint renters cannot see each other's raw verification
documents by default")."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.crud.identity_verification import get_valid_identity_credential, list_user_identity_verifications
from app.crud.market_policy import resolve_market_policy
from app.crud.occupancy_eligibility import list_occupancy_eligibility_checks_for_party
from app.db.session import get_db
from app.models.party import Party
from app.models.user_account import UserAccount
from app.schemas.verification import (
    OCCUPANCY_ELIGIBILITY_SHARING_SCOPE,
    RenterVerificationStatus,
    RenterVerificationStatusItem,
)

router = APIRouter(prefix="/api/users/verification-status", tags=["user-verification-status"], dependencies=[Depends(get_current_user)])

# AC-42: "Renter UI explains purpose, data sharing, retention profile and
# alternative method for each requirement." sharing_scope reuses the same
# fixed, code-enforced facts from schemas/verification.py (AC-18) rather
# than duplicating them -- one source of truth for "who can see this."
_IDENTITY_SHARING_SCOPE = "Visible to you and admins. Never visible to a Host."


def _retention_note(db: Session, jurisdiction_code: str | None) -> str:
    if not jurisdiction_code:
        return "Retention period depends on your jurisdiction and has not been configured yet."
    try:
        policy = resolve_market_policy(db, jurisdiction_code)
    except HTTPException:
        return "Retention period depends on your jurisdiction and has not been configured yet."
    return f"Evidence is retained for {policy.identity_evidence_retention_days} days, then automatically deleted."


@router.get("", response_model=RenterVerificationStatus)
def get_my_verification_status(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.party_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "User has no associated party")

    party_row = db.get(Party, user.party_id)
    jurisdiction_code = party_row.jurisdiction if party_row else None

    identity_credential = get_valid_identity_credential(db, user.party_id)
    latest_identity_submission = next(iter(list_user_identity_verifications(db, user)), None)
    identity_status = "verified" if identity_credential else (latest_identity_submission.status if latest_identity_submission else "not_submitted")

    identity_alternative_note = ""
    if identity_status == "additional_evidence_required":
        identity_alternative_note = "You can submit an alternate document type, or a different form of the same document, if your original was unsupported."
    elif identity_status in ("not_submitted", "rejected", "expired"):
        identity_alternative_note = "Multiple accepted document types are available (passport, driving licence, national ID, and more)."

    identity_item = RenterVerificationStatusItem(
        requirement_code="IDENTITY",
        status=identity_status,
        expires_at=identity_credential.expires_at if identity_credential else None,
        explanation="Confirms who you are before you can apply to rent or publish a listing.",
        sharing_scope=_IDENTITY_SHARING_SCOPE,
        retention_note=_retention_note(db, jurisdiction_code),
        alternative_method_note=identity_alternative_note,
    )

    occupancy_items = [
        RenterVerificationStatusItem(
            requirement_code="OCCUPANCY_ELIGIBILITY",
            status=check.status,
            expires_at=check.follow_up_due_at,
            jurisdiction_code=check.jurisdiction_code,
            explanation=f"Confirms your right to occupy a property in {check.jurisdiction_code} before move-in.",
            sharing_scope=OCCUPANCY_ELIGIBILITY_SHARING_SCOPE,
            retention_note=_retention_note(db, check.jurisdiction_code),
            alternative_method_note="Available via a digital share code or a manual document check -- whichever suits you.",
        )
        for check in list_occupancy_eligibility_checks_for_party(db, user.party_id)
    ]

    return RenterVerificationStatus(identity=identity_item, occupancy_eligibility=occupancy_items)
