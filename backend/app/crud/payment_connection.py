"""ZR-PAY-LINK-003 Section 3.1: the consolidated payment-connection status
view -- "is rent collection actually usable for this room yet, and why not
if not." Computed live from payment_recipient_authority + rental_payment's
own RentalPaymentInstruction rather than persisted in a new table (see this
repo's own ZR-PAY-LINK-003 gap-analysis plan for the rationale): both those
tables are already the authoritative source for recipient/destination state,
so a third copy of that state would just be a new place for it to drift.

CLOSED (Section 3.1: "Rental ended or relationship terminated") is not
derived here -- no room/property lifecycle flag exists yet to key it off.
Every room this resolves for is treated as potentially open."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from sqlalchemy import select

from app.models.authority_record import AuthorityRecord
from app.models.room import Room
from app.models.user_account import UserAccount
from app.schemas.payment_connection import PaymentConnectionRead

PAYMENT_CONNECTION_STATES = (
    "DRAFT", "RECIPIENT_SETUP_REQUIRED", "PENDING_VERIFICATION", "ACTIVE", "SUSPENDED",
)


def get_payment_connection_for_room(db: Session, room: Room) -> PaymentConnectionRead:
    from app.crud.rental_payment import get_active_rental_payment_instruction, list_rental_payment_instructions_for_party
    from app.crud.rental_payment import resolve_rent_recipient_party_id

    # Who receives rent is decided by the room's listing authority (the
    # admin-verified right to list it) -- there is no separate
    # payment-recipient step.
    authority = db.scalar(
        select(AuthorityRecord).where(AuthorityRecord.room_id == room.id).order_by(AuthorityRecord.id.desc())
    )
    recipient_party_id = resolve_rent_recipient_party_id(db, room)

    # Rent is paid directly to the recipient by bank transfer, UPI or cash
    # (no card / payment-provider rail): the room is payment-ready once the
    # recipient's own payment instructions are active.
    destination = None
    if recipient_party_id is not None:
        destination = get_active_rental_payment_instruction(db, recipient_party_id)
        if destination is None:
            candidates = list_rental_payment_instructions_for_party(db, recipient_party_id)
            latest = candidates[0] if candidates else None
            if latest is not None and latest.status in ("PENDING_VERIFICATION", "PENDING_REVIEW", "REJECTED"):
                destination = latest

    state = _derive_state(authority, destination)

    if destination is not None:
        destination_method, destination_status = destination.method, destination.status
        destination_masked = f"******{destination.account_identifier_last4}"
    else:
        destination_method = destination_status = destination_masked = None

    return PaymentConnectionRead(
        room_id=room.id,
        state=state,
        recipient_party_id=authority.party_id if authority else recipient_party_id,
        recipient_authority_id=authority.id if authority else None,
        recipient_relationship_type=authority.relationship_type if authority else None,
        recipient_authority_status=authority.status if authority else None,
        recipient_verified_at=authority.verified_at if authority else None,
        recipient_expires_at=authority.expires_at if authority else None,
        destination_method=destination_method,
        destination_status=destination_status,
        destination_account_identifier_masked=destination_masked,
    )


def get_payment_connection_for_room_owned_by(db: Session, user: UserAccount, room: Room) -> PaymentConnectionRead:
    """Same ownership check as
    payment_recipient_authority.list_payment_recipient_authorities_for_room_owned_by
    -- only the room's own owner may view its payment-connection status."""
    if not user.party_id or room.property.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view the payment connection for your own room")
    return get_payment_connection_for_room(db, room)


def _derive_state(authority, destination) -> str:
    if authority is None:
        return "DRAFT"
    # Listing authority still being checked by an admin.
    if authority.status in ("not_started", "pending", "review_required", "conflict"):
        return "PENDING_VERIFICATION"
    if authority.status in ("failed", "revoked", "expired"):
        return "SUSPENDED"
    # "verified" (or "expiring" -- still valid) from here on.
    expires_at = authority.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        return "SUSPENDED"
    if destination is None:
        return "RECIPIENT_SETUP_REQUIRED"
    if destination.status == "ACTIVE":
        return "ACTIVE"
    if destination.status in ("PENDING_VERIFICATION", "PENDING_REVIEW"):
        return "PENDING_VERIFICATION"
    if destination.status == "REJECTED":
        return "SUSPENDED"
    return "RECIPIENT_SETUP_REQUIRED"
