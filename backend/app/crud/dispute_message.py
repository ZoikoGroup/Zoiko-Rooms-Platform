"""ZR-ENG-CLR-010 Section 11/19/23: structured, moderated case-room
messaging. Section 19: 'Safety cases can disable party-to-party
messaging' -- assert_messaging_allowed blocks a RENTER/HOST sender
outright once the case carries a PROTECTED_SAFETY claim or an A6
authority-class claim; an ADMIN can always still post. Phase 15 adds a
second, narrower gate on top: a sender whose own DisputeParty row carries
a non-empty communication_restrictions is blocked even on an otherwise
ordinary case -- Section 19's 'Prohibit retaliatory account action...
merely because a party raised a good-faith complaint' cuts both ways: a
restriction is a recorded admin decision on a specific party, never an
automatic consequence of raising a claim."""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import dispute_party as party_crud
from app.models.admin_user import AdminUser
from app.models.dispute import DisputeResolutionCase
from app.models.dispute_message import DisputeCaseMessage
from app.models.guest import Guest

_PARTY_VISIBLE_ONLY = ("PARTY_VISIBLE",)


def _case_is_safety_flagged(case: DisputeResolutionCase) -> bool:
    return any(claim.claim_family == "PROTECTED_SAFETY" or claim.authority_class == "A6" for claim in case.claims)


def assert_messaging_allowed(
    db: Session, case: DisputeResolutionCase, *, sender_role: str, guest: Guest | None = None, party_id: int | None = None,
) -> None:
    if sender_role == "ADMIN":
        return
    if _case_is_safety_flagged(case):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Direct party-to-party messaging is disabled on this case for safety reasons; contact Zoiko support instead",
        )
    restriction = party_crud.communication_restrictions_for(db, case, guest=guest, party_id=party_id)
    if restriction:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Direct messaging is restricted for this case: {restriction}")


def post_message(
    db: Session,
    case: DisputeResolutionCase,
    *,
    body: str,
    guest: Guest | None = None,
    party_id: int | None = None,
    admin: AdminUser | None = None,
    visibility_class: str | None = None,
) -> DisputeCaseMessage:
    sender_count = sum(1 for u in (guest, party_id, admin) if u is not None)
    if sender_count != 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A message must be sent by exactly one of a renter, a host or an admin")
    if not body.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Message body cannot be empty")

    sender_role = "ADMIN" if admin is not None else ("RENTER" if guest is not None else "HOST")
    assert_messaging_allowed(db, case, sender_role=sender_role, guest=guest, party_id=party_id)

    # Only an admin sender may post INTERNAL_ONLY -- a renter/host can never
    # write a message the other party won't eventually see.
    resolved_visibility = visibility_class if (admin is not None and visibility_class == "INTERNAL_ONLY") else "PARTY_VISIBLE"

    message = DisputeCaseMessage(
        case_id=case.id,
        sender_role=sender_role,
        sender_guest_id=guest.id if guest is not None else None,
        sender_party_id=party_id,
        sender_admin_id=admin.id if admin is not None else None,
        body=body,
        visibility_class=resolved_visibility,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def get_message_or_404(db: Session, message_id: int) -> DisputeCaseMessage:
    message = db.get(DisputeCaseMessage, message_id)
    if not message:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    return message


def list_messages_for_case(db: Session, case: DisputeResolutionCase, *, viewer_is_admin: bool) -> list[DisputeCaseMessage]:
    query = select(DisputeCaseMessage).where(DisputeCaseMessage.case_id == case.id).order_by(DisputeCaseMessage.created_at.asc())
    messages = list(db.scalars(query))
    if viewer_is_admin:
        return messages
    # Non-admin viewers never see INTERNAL_ONLY messages, and never see a
    # HIDDEN message's content -- same disclosure/moderation discipline as
    # evidence's disclosure_class gate.
    return [m for m in messages if m.visibility_class in _PARTY_VISIBLE_ONLY and m.moderation_state == "VISIBLE"]


def moderate_message(db: Session, message: DisputeCaseMessage, admin: AdminUser, *, hidden: bool) -> DisputeCaseMessage:
    message.moderation_state = "HIDDEN" if hidden else "VISIBLE"
    message.moderated_by_admin_id = admin.id
    message.moderated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(message)
    return message
