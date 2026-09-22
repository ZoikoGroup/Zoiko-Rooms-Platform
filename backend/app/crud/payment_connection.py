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

from app.crud.payment_recipient_authority import get_latest_payment_recipient_authority_for_room
from app.crud.rental_payment_provider_account import get_charge_ready_provider_account_for_party
from app.models.room import Room
from app.models.user_account import UserAccount
from app.schemas.payment_connection import PaymentConnectionRead

PAYMENT_CONNECTION_STATES = (
    "DRAFT", "RECIPIENT_SETUP_REQUIRED", "PENDING_VERIFICATION", "ACTIVE", "SUSPENDED",
)


def get_payment_connection_for_room(db: Session, room: Room) -> PaymentConnectionRead:
    from app.crud.market_policy import DEFAULT_JURISDICTION, resolve_available_payment_methods
    from app.crud.rental_payment import get_active_rental_payment_instruction, list_rental_payment_instructions_for_party
    from app.crud.rental_payment import resolve_rent_recipient_party_id

    authority = get_latest_payment_recipient_authority_for_room(db, room.id)
    recipient_party_id = resolve_rent_recipient_party_id(db, room)

    # ZR-PAY-LINK-003 Section 22/AC-12: a charge-ready provider account only
    # counts as this room's destination when the online rail is actually
    # permitted in this room's own jurisdiction -- same
    # resolve_available_payment_methods gate
    # crud/external_payment_session.py:create_session independently
    # enforces before actually letting a session start; this is what keeps
    # the connection VIEW honest about it too, rather than showing ACTIVE
    # for a rail that would then 409 the moment a tenant tried to use it.
    jurisdiction_code = (room.property.jurisdiction_code if room.property else None) or DEFAULT_JURISDICTION
    online_rail_permitted = "CARD" in resolve_available_payment_methods(db, jurisdiction_code)

    # ZR-PAY-LINK-003 Section 6: a charge-ready online provider account is
    # checked FIRST and, when present, wins over the direct-instruction
    # destination below -- a recipient who has completed Stripe Connect
    # onboarding is unambiguously "ready," whereas a direct instruction can
    # sit at PENDING_VERIFICATION/PENDING_REVIEW indefinitely. Either rail
    # alone is enough for ACTIVE (Section 6: 'Manual bank transfer and
    # provider-hosted digital payment are two rails over the same
    # relationship').
    provider_account = None
    destination = None
    if recipient_party_id is not None:
        if online_rail_permitted:
            provider_account = get_charge_ready_provider_account_for_party(db, recipient_party_id)
        if provider_account is None:
            destination = get_active_rental_payment_instruction(db, recipient_party_id)
            if destination is None:
                candidates = list_rental_payment_instructions_for_party(db, recipient_party_id)
                latest = candidates[0] if candidates else None
                if latest is not None and latest.status in ("PENDING_VERIFICATION", "PENDING_REVIEW", "REJECTED"):
                    destination = latest

    state = _derive_state(authority, provider_account, destination)

    if provider_account is not None:
        destination_method, destination_status, destination_masked = "ONLINE_PROVIDER", "COMPLETE", None
    elif destination is not None:
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


def _derive_state(authority, provider_account, destination) -> str:
    if authority is None:
        return "DRAFT"
    # ZR-PAY-LINK-003 Section 14.1: a change still awaiting the submitter's
    # own step-up code confirmation is no more usable than a plain pending
    # admin-verification row -- same bucket.
    if authority.status in ("pending", "pending_step_up"):
        return "PENDING_VERIFICATION"
    if authority.status in ("failed", "revoked"):
        return "SUSPENDED"
    # authority.status == "verified" from here on.
    if authority.expires_at is not None and authority.expires_at <= datetime.now(timezone.utc):
        return "SUSPENDED"
    if provider_account is not None:
        return "ACTIVE"
    if destination is None:
        return "RECIPIENT_SETUP_REQUIRED"
    if destination.status == "ACTIVE":
        return "ACTIVE"
    if destination.status in ("PENDING_VERIFICATION", "PENDING_REVIEW"):
        return "PENDING_VERIFICATION"
    if destination.status == "REJECTED":
        return "SUSPENDED"
    return "RECIPIENT_SETUP_REQUIRED"
