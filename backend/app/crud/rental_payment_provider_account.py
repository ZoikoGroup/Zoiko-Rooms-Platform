"""ZR-PAY-LINK-003 Section 6/Wireframe C: self-service Stripe Connect
onboarding for a payment recipient's own rent-collection account. Mirrors
crud/host_stripe_account.py's own four functions closely, but self-service
(the calling Party itself, not an AdminUser deciding on a provider's behalf)
-- see models/rental_payment_provider_account.py's own docstring for why
this is a separate table/module rather than a reuse of HostStripeAccount.

request_account_change/confirm_account_change/resend_account_change_code
(Section 14.1's governed-change flow) mirror
crud/rental_payment.py:submit_rental_payment_instruction's own step-up-code/
risk-flag/supersede/notify-affected-tenants shape -- this is a destination
object, same as RentalPaymentInstruction, not an authority claim, so it
follows that precedent rather than PaymentRecipientAuthority's."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.mailer import send_rental_payment_provider_account_change_verification_code_email
from app.crud import notification as notif_crud
from app.crud.events import emit_event
from app.models.party import Party
from app.models.rental_payment_provider_account import RentalPaymentProviderAccount
from app.models.user_account import UserAccount
from app.services import stripe_client

# ZR-PAY-LINK-003 Section 14.1: same step-up-code window/attempt-limit shape
# as rental_payment.py's INSTRUCTION_VERIFICATION_CODE_EXPIRE_MINUTES/
# INSTRUCTION_MAX_VERIFICATION_ATTEMPTS, duplicated (not imported) to keep
# this module independent -- see this module's own docstring.
ACCOUNT_CHANGE_CODE_EXPIRE_MINUTES = 15
ACCOUNT_CHANGE_MAX_VERIFICATION_ATTEMPTS = 5
ACCOUNT_CHANGE_RECENT_CREDENTIAL_CHANGE_RISK_WINDOW_HOURS = 24


def get_for_party(db: Session, party_id: int) -> RentalPaymentProviderAccount | None:
    """Only the CURRENT row -- SUPERSEDED history is excluded, matching the
    partial unique index (models/rental_payment_provider_account.py) that
    guarantees at most one non-SUPERSEDED row per party, so this can never
    raise MultipleResultsFound even after a confirmed account change."""
    return db.scalar(
        select(RentalPaymentProviderAccount).where(
            RentalPaymentProviderAccount.party_id == party_id, RentalPaymentProviderAccount.status != "SUPERSEDED",
        )
    )


def get_charge_ready_provider_account_for_party(db: Session, party_id: int) -> RentalPaymentProviderAccount | None:
    """The one query crud/external_payment_session.py:create_session goes
    through to decide whether the online rail is even offerable -- never
    re-derived inline, same discipline every other *_for_party/*_for_room
    resolver in this codebase already follows. A row existing is not enough
    on its own (Wireframe C: 'No rental payment can start while recipient
    onboarding, capability... are incomplete') -- only COMPLETE + actually
    charges_enabled counts."""
    account = get_for_party(db, party_id)
    if account is not None and account.status == "COMPLETE" and account.charges_enabled:
        return account
    return None


def _sync_status(account: RentalPaymentProviderAccount) -> None:
    if account.details_submitted and account.charges_enabled and account.payouts_enabled:
        account.status = "COMPLETE"
    elif account.status != "COMPLETE":
        account.status = "ONBOARDING"


def create_connected_account(db: Session, party: Party, *, country: str, email: str) -> RentalPaymentProviderAccount:
    if get_for_party(db, party.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have a connected payment account")

    stripe_account_id = stripe_client.create_connected_account(
        country=country, email=email, metadata={"domain": "rental_payment", "party_id": str(party.id)},
    )
    account = RentalPaymentProviderAccount(party_id=party.id, stripe_account_id=stripe_account_id, status="ONBOARDING")
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def create_onboarding_link(account: RentalPaymentProviderAccount) -> str:
    return stripe_client.create_account_onboarding_link(stripe_account_id=account.stripe_account_id)


def refresh_account_status(db: Session, account: RentalPaymentProviderAccount) -> RentalPaymentProviderAccount:
    """Re-reads the account's status from Stripe -- a no-op returning {} when
    Stripe isn't configured (stripe_client.retrieve_account_status's own
    docstring), see simulate_onboarding_complete for the no-real-Stripe
    equivalent a dev/test flow uses instead."""
    status_fields = stripe_client.retrieve_account_status(stripe_account_id=account.stripe_account_id)
    for field, value in status_fields.items():
        setattr(account, field, value)
    _sync_status(account)
    account.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(account)
    return account


def simulate_onboarding_complete(db: Session, account: RentalPaymentProviderAccount) -> RentalPaymentProviderAccount:
    """Dev/test-only path: marks an account as if Stripe's own hosted
    onboarding had fully completed, without needing a real Stripe account to
    walk through it. Refuses outright once real Stripe credentials are
    configured -- at that point status must come from Stripe itself
    (refresh_account_status / a future account.updated webhook), never be
    hand-set, same guard as crud/host_stripe_account.py's own equivalent."""
    if stripe_client.is_configured():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Real Stripe credentials are configured -- account status must come from Stripe, not be simulated",
        )
    account.details_submitted = True
    account.charges_enabled = True
    account.payouts_enabled = True
    account.status = "COMPLETE"
    account.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(account)
    return account


def _hash_account_change_code(raw_code: str) -> str:
    return hashlib.sha256(raw_code.encode("utf-8")).hexdigest()


def _generate_and_send_account_change_code(account: RentalPaymentProviderAccount, user: UserAccount) -> str:
    """Best-effort send: a delivery failure must never block the request,
    same discipline as rental_payment.py:_generate_and_send_instruction_code."""
    raw_code = f"{secrets.randbelow(1_000_000):06d}"
    account.verification_code_hash = _hash_account_change_code(raw_code)
    account.verification_code_expires_at = datetime.now(timezone.utc) + timedelta(minutes=ACCOUNT_CHANGE_CODE_EXPIRE_MINUTES)
    account.verification_attempts = 0
    send_rental_payment_provider_account_change_verification_code_email(
        user.email, user.full_name, raw_code, ACCOUNT_CHANGE_CODE_EXPIRE_MINUTES,
    )
    return raw_code


def _assess_account_change_risk(user: UserAccount) -> tuple[bool, str]:
    """Same recent-password-change signal as
    rental_payment.py:_assess_instruction_change_risk -- see that
    function's own docstring for why this is the one real signal this
    build has."""
    if user.password_changed_at:
        age = datetime.now(timezone.utc) - user.password_changed_at
        if age <= timedelta(hours=ACCOUNT_CHANGE_RECENT_CREDENTIAL_CHANGE_RISK_WINDOW_HOURS):
            return True, "Account password was changed within the last 24 hours"
    return False, ""


def request_account_change(db: Session, account: RentalPaymentProviderAccount, user: UserAccount) -> str:
    """ZR-PAY-LINK-003 Section 14.1: 'Step-up authentication is mandatory'
    for a destination change. Mailed to the party's own account -- proving
    they control it, not proving anything about the new Stripe account
    (Stripe's own onboarding/KYC does that once confirm_account_change
    actually creates it). Deliberately does not change `account.status` --
    see this model's own docstring for why the existing destination must
    stay exactly as usable as it already was while a change is merely
    requested, not yet confirmed."""
    raw_code = _generate_and_send_account_change_code(account, user)
    db.commit()
    return raw_code


def resend_account_change_code(db: Session, account: RentalPaymentProviderAccount, user: UserAccount) -> str:
    if not account.verification_code_hash:
        raise HTTPException(status.HTTP_409_CONFLICT, "No account change is currently pending")
    raw_code = _generate_and_send_account_change_code(account, user)
    db.commit()
    return raw_code


def _notify_affected_tenants_account_changed(db: Session, party_id: int) -> None:
    """ZR-PAY-LINK-003 Section 14.1/23: same technique as
    rental_payment.py:_notify_affected_tenants_instructions_changed -- the
    tenants of this party's still-open obligations are the ones with
    something to review before their next payment."""
    from app.crud.rental_payment import list_obligations_for_recipient
    from app.models.guest import Guest

    tenant_guest_ids = {
        o.tenant_guest_id for o in list_obligations_for_recipient(db, party_id) if o.status not in ("WAIVED", "CANCELLED")
    }
    for guest_id in tenant_guest_ids:
        guest = db.get(Guest, guest_id)
        if guest:
            notif_crud.notify_user_by_guest(
                db, guest, title="Payment account changed",
                message=(
                    "Your landlord or agent changed the account that receives online rent payments. Review the "
                    "updated details in Payments before your next payment."
                ),
                notification_type="rental_payment_provider_account.changed",
                related_entity_type="rental_payment_provider_account", related_entity_id=str(party_id),
            )


def confirm_account_change(
    db: Session, account: RentalPaymentProviderAccount, user: UserAccount, raw_code: str, *, country: str, email: str,
) -> RentalPaymentProviderAccount:
    """The strong-auth step itself, same shape as
    crud/rental_payment.py:confirm_rental_payment_instruction. Success
    supersedes the current account and creates a brand-new one via a real
    Stripe API call -- never reuses the old stripe_account_id, since the
    whole point of a change is that the old destination may no longer be
    trusted."""
    if not account.verification_code_hash or not account.verification_code_expires_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "No account change is currently pending")
    if account.verification_code_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Verification code has expired -- request a new one")
    if account.verification_attempts >= ACCOUNT_CHANGE_MAX_VERIFICATION_ATTEMPTS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many incorrect attempts -- request a new code")

    if _hash_account_change_code(raw_code.strip()) != account.verification_code_hash:
        account.verification_attempts += 1
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Incorrect verification code")

    is_high_risk, high_risk_reason = _assess_account_change_risk(user)

    # Supersede (and flush) the old row BEFORE inserting the new one -- the
    # partial unique index only allows one non-SUPERSEDED row per party at
    # a time, and an INSERT racing ahead of this UPDATE within the same
    # flush would violate it.
    account.status = "SUPERSEDED"
    account.verification_code_hash = None
    account.verification_code_expires_at = None
    db.flush()

    new_stripe_account_id = stripe_client.create_connected_account(
        country=country, email=email, metadata={"domain": "rental_payment", "party_id": str(account.party_id)},
    )
    new_account = RentalPaymentProviderAccount(
        party_id=account.party_id, stripe_account_id=new_stripe_account_id, status="ONBOARDING",
        is_high_risk=is_high_risk, high_risk_reason=high_risk_reason,
    )
    db.add(new_account)
    db.commit()
    db.refresh(new_account)

    emit_event(
        db, "payment_destination.changed", "rental_payment_provider_account", str(new_account.id),
        {"partyId": account.party_id, "previousAccountId": account.id},
        actor_kind="party", actor_id=str(account.party_id),
    )
    db.commit()

    _notify_affected_tenants_account_changed(db, account.party_id)
    db.commit()
    return new_account
