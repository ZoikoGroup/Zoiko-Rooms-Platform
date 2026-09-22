"""ZR-PAY-LINK-003 Section 1.1/2: "'Authority to list' and 'authority to
receive payments' are separate claims." Mirrors crud/authority.py's own
shape closely (same party_id+room_id scoping, same admin-decision workflow)
applied to a different claim -- see PaymentRecipientAuthority's own model
docstring for why this is its own table rather than a new field/status on
AuthorityRecord."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.mailer import send_payment_recipient_authority_change_verification_code_email
from app.crud import notification as notif_crud
from app.crud.audit import log_audit_event
from app.models.admin_user import AdminUser
from app.models.guest import Guest
from app.models.payment_recipient_authority import PaymentRecipientAuthority
from app.models.room import Room
from app.models.user_account import UserAccount

PAYMENT_RECIPIENT_AUTHORITY_VALIDITY_DAYS = 365
# ZR-PAY-LINK-003 Section 14.1: same step-up-code window/attempt-limit shape
# as rental_payment.py's INSTRUCTION_VERIFICATION_CODE_EXPIRE_MINUTES/
# INSTRUCTION_MAX_VERIFICATION_ATTEMPTS, duplicated (not imported) to keep
# this module independent of that one -- see this module's own docstring.
RECIPIENT_CHANGE_CODE_EXPIRE_MINUTES = 15
RECIPIENT_CHANGE_MAX_VERIFICATION_ATTEMPTS = 5
RECIPIENT_CHANGE_RECENT_CREDENTIAL_CHANGE_RISK_WINDOW_HOURS = 24


def list_payment_recipient_authorities(db: Session, room_id: int | None = None) -> list[PaymentRecipientAuthority]:
    query = select(PaymentRecipientAuthority).order_by(PaymentRecipientAuthority.id)
    if room_id is not None:
        query = query.where(PaymentRecipientAuthority.room_id == room_id)
    return list(db.scalars(query))


def get_payment_recipient_authority_or_404(db: Session, authority_id: int) -> PaymentRecipientAuthority:
    record = db.get(PaymentRecipientAuthority, authority_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment recipient authority not found")
    return record


def get_valid_payment_recipient_authority_for_room(db: Session, room_id: int) -> PaymentRecipientAuthority | None:
    """The one query resolve_rent_recipient_party_id (crud/rental_payment.py)
    goes through -- never re-derived inline, same discipline every other
    *_for_room resolver in this codebase already follows. Deliberately NOT
    wired into crud/listing.py:check_publish_eligibility -- payment-receipt
    authority gates the rent-collection connection for an existing tenancy
    (ZR-PAY-LINK-003), not whether a listing may go live (ZR-PAY-002's own,
    separate checklist); conflating the two would blur exactly the
    ownership-of-funds-free domain boundary Section 12.1 draws."""
    now = datetime.now(timezone.utc)
    return db.scalar(
        select(PaymentRecipientAuthority)
        .where(
            PaymentRecipientAuthority.room_id == room_id,
            PaymentRecipientAuthority.status == "verified",
            (PaymentRecipientAuthority.expires_at.is_(None)) | (PaymentRecipientAuthority.expires_at > now),
        )
        .order_by(PaymentRecipientAuthority.id.desc())
    )


def get_latest_payment_recipient_authority_for_room(db: Session, room_id: int) -> PaymentRecipientAuthority | None:
    """Sibling to get_valid_payment_recipient_authority_for_room above, but
    returns the most recent row of ANY status -- crud/payment_connection.py's
    state derivation needs to distinguish 'never declared' (DRAFT) from
    'declared but pending/failed/revoked/expired' (PENDING_VERIFICATION/
    SUSPENDED), which get_valid_... alone can't tell apart since it only
    ever matches status == 'verified'."""
    return db.scalar(
        select(PaymentRecipientAuthority)
        .where(PaymentRecipientAuthority.room_id == room_id)
        .order_by(PaymentRecipientAuthority.id.desc())
    )


def _hash_recipient_change_code(raw_code: str) -> str:
    return hashlib.sha256(raw_code.encode("utf-8")).hexdigest()


def _generate_and_send_recipient_change_code(db: Session, record: PaymentRecipientAuthority, user: UserAccount) -> str:
    """Section 14.1's step-up code, mailed to the SUBMITTING host's own
    account -- proving they control it, not proving anything about the new
    recipient (that's still the admin's own verify step). Best-effort send:
    a delivery failure must never block submission/resend, same discipline
    as rental_payment.py:_generate_and_send_instruction_code."""
    raw_code = f"{secrets.randbelow(1_000_000):06d}"
    record.verification_code_hash = _hash_recipient_change_code(raw_code)
    record.verification_code_expires_at = datetime.now(timezone.utc) + timedelta(minutes=RECIPIENT_CHANGE_CODE_EXPIRE_MINUTES)
    record.verification_attempts = 0
    send_payment_recipient_authority_change_verification_code_email(
        user.email, user.full_name, raw_code, RECIPIENT_CHANGE_CODE_EXPIRE_MINUTES,
    )
    return raw_code


def _assess_recipient_change_risk(user: UserAccount) -> tuple[bool, str]:
    """Same recent-password-change signal as
    rental_payment.py:_assess_instruction_change_risk -- see that function's
    own docstring for why this is the one real signal this build has."""
    if user.password_changed_at:
        age = datetime.now(timezone.utc) - user.password_changed_at
        if age <= timedelta(hours=RECIPIENT_CHANGE_RECENT_CREDENTIAL_CHANGE_RISK_WINDOW_HOURS):
            return True, "Account password was changed within the last 24 hours"
    return False, ""


def _notify_affected_tenants_recipient_changed(db: Session, previous_recipient_party_id: int) -> None:
    """ZR-PAY-LINK-003 Section 14.1/23: 'Affected renters receive an in-app
    notification ... that payment instructions changed.' Same technique as
    rental_payment.py:_notify_affected_tenants_instructions_changed -- the
    tenants of the OLD recipient's still-open obligations are the ones with
    something to review before their next payment (obligations already
    created under the old recipient never retroactively change payee, per
    resolve_rent_recipient_party_id's own docstring, so this is who is
    actually affected)."""
    from app.crud.rental_payment import list_obligations_for_recipient

    tenant_guest_ids = {
        o.tenant_guest_id for o in list_obligations_for_recipient(db, previous_recipient_party_id)
        if o.status not in ("WAIVED", "CANCELLED")
    }
    for guest_id in tenant_guest_ids:
        guest = db.get(Guest, guest_id)
        if guest:
            notif_crud.notify_user_by_guest(
                db, guest, title="Payment recipient changed",
                message=(
                    "Payment instructions changed for this rental. Review the updated recipient and instructions "
                    "in Payments before sending money."
                ),
                notification_type="payment_recipient_authority.changed",
                related_entity_type="payment_recipient_authority", related_entity_id=str(previous_recipient_party_id),
            )


def declare_payment_recipient_authority(
    db: Session, user: UserAccount, room: Room, *,
    recipient_party_id: int, relationship_type: str, evidence_ref: str,
) -> tuple[PaymentRecipientAuthority, str | None]:
    """Host self-service submission -- only the room's own owner may submit
    this claim (same ownership-check shape as
    crud/authority.py:declare_authority_record), but the claimed
    recipient_party_id may be a DIFFERENT party than the submitter's own
    (Wireframe A: 'My verified organization' / 'An authorized agent or
    property manager' / 'Another authorized recipient') -- ZR-PAY-LINK-003
    Section 1.1 explicitly requires this to be possible, unlike list
    authority which is always the submitter's own claim about themselves.

    This does NOT by itself make the claim trustworthy -- it lands in
    'pending' exactly like declare_authority_record's own submissions, and
    only an admin's own independent verify_payment_recipient_authority
    decision (never this submission alone) can make it actually govern who
    receives rent (see crud/rental_payment.py:resolve_rent_recipient_party_id).
    This is the same trust model Section 2's 'no free-form linking' already
    relies on for list authority -- the claim is freely submittable, the
    verification is not.

    Section 14.1: when the room ALREADY has a live verified recipient, this
    is a *change*, not a first-time setup -- it lands in 'pending_step_up'
    instead, and the returned raw_code (mailed to the submitter, never
    persisted in the clear) must be confirmed via
    confirm_payment_recipient_authority_change before it even reaches
    'pending'. A room's first-ever declaration stays exactly as before
    (straight to 'pending', raw_code is None) -- see this module's own
    docstring for why that stays ungated."""
    if not user.party_id or room.property.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only set up payments for your own room")

    is_change = get_valid_payment_recipient_authority_for_room(db, room.id) is not None
    record = PaymentRecipientAuthority(
        party_id=recipient_party_id, room_id=room.id,
        relationship_type=relationship_type, evidence_ref=evidence_ref,
        status="pending_step_up" if is_change else "pending",
    )
    db.add(record)
    db.flush()

    raw_code = None
    if is_change:
        raw_code = _generate_and_send_recipient_change_code(db, record, user)
    db.commit()
    db.refresh(record)

    log_audit_event(
        db, None, "payment_recipient_authority.declare", "payment_recipient_authority", str(record.id),
        reason=f"user:{user.id}; room={room.id}; recipient_party={recipient_party_id}; relationship={relationship_type}",
    )
    db.commit()
    return record, raw_code


def resend_payment_recipient_authority_change_code(db: Session, record: PaymentRecipientAuthority, user: UserAccount) -> str:
    if record.status != "pending_step_up":
        raise HTTPException(status.HTTP_409_CONFLICT, "This change is not awaiting step-up verification")
    raw_code = _generate_and_send_recipient_change_code(db, record, user)
    db.commit()
    return raw_code


def confirm_payment_recipient_authority_change(
    db: Session, record: PaymentRecipientAuthority, user: UserAccount, raw_code: str,
) -> PaymentRecipientAuthority:
    """The strong-auth step itself (Section 14.1's 'step-up authentication is
    mandatory'), same shape as
    rental_payment.py:confirm_rental_payment_instruction. Success only ever
    reaches 'pending' -- the existing, unchanged admin
    verify_payment_recipient_authority call is still required after this,
    same as it always was; this just makes that pending row step-up-confirmed
    and risk-flagged first."""
    if record.status != "pending_step_up":
        raise HTTPException(status.HTTP_409_CONFLICT, "This change is not awaiting step-up verification")
    if not record.verification_code_expires_at or record.verification_code_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Verification code has expired -- request a new one")
    if record.verification_attempts >= RECIPIENT_CHANGE_MAX_VERIFICATION_ATTEMPTS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many incorrect attempts -- request a new code")

    if _hash_recipient_change_code(raw_code.strip()) != record.verification_code_hash:
        record.verification_attempts += 1
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Incorrect verification code")

    is_high_risk, high_risk_reason = _assess_recipient_change_risk(user)
    record.is_high_risk = is_high_risk
    record.high_risk_reason = high_risk_reason
    record.status = "pending"
    db.commit()
    db.refresh(record)

    log_audit_event(
        db, None, "payment_recipient_authority.change_confirmed", "payment_recipient_authority", str(record.id),
        reason=f"is_high_risk={is_high_risk}",
    )
    db.commit()
    return record


def list_payment_recipient_authorities_for_room_owned_by(
    db: Session, user: UserAccount, room: Room,
) -> list[PaymentRecipientAuthority]:
    if not user.party_id or room.property.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view payment recipient authority for your own room")
    return list(
        db.scalars(
            select(PaymentRecipientAuthority)
            .where(PaymentRecipientAuthority.room_id == room.id)
            .order_by(PaymentRecipientAuthority.id.desc())
        )
    )


def verify_payment_recipient_authority(
    db: Session, record: PaymentRecipientAuthority, verifier: AdminUser,
) -> PaymentRecipientAuthority:
    """Section 14.1: only a 'pending' row may be verified -- rejects a still
    step-up-unconfirmed 'pending_step_up' row (closing what would otherwise
    be a way to skip the step-up gate entirely) as well as anything already
    decided (verified/failed/revoked).

    If this room already had a DIFFERENT live verified recipient, this is a
    genuine change taking effect -- notify that recipient's tenants (Section
    14.1/23) before it's overwritten by the new one below. A room's first-ever
    verification (previous is None) has nothing to notify about."""
    if record.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a pending payment recipient authority can be verified")

    previous = get_valid_payment_recipient_authority_for_room(db, record.room_id)

    now = datetime.now(timezone.utc)
    record.status = "verified"
    record.verified_at = now
    record.expires_at = now + timedelta(days=PAYMENT_RECIPIENT_AUTHORITY_VALIDITY_DAYS)
    record.verifier_admin_id = verifier.id
    db.commit()
    db.refresh(record)

    if previous is not None and previous.party_id != record.party_id:
        _notify_affected_tenants_recipient_changed(db, previous.party_id)
        db.commit()
    return record


def reject_payment_recipient_authority(
    db: Session, record: PaymentRecipientAuthority, verifier: AdminUser,
) -> PaymentRecipientAuthority:
    record.status = "failed"
    record.verifier_admin_id = verifier.id
    db.commit()
    db.refresh(record)
    return record


def revoke_payment_recipient_authority(
    db: Session, record: PaymentRecipientAuthority, revoker: AdminUser,
) -> PaymentRecipientAuthority:
    """Counterpart to reject_ above for a claim already 'verified' -- e.g.
    the agent relationship ended, or evidence later turns out to be
    fraudulent. get_valid_payment_recipient_authority_for_room only ever
    matches status == 'verified', so this takes effect immediately, same as
    an expiry -- no separate gate change needed. Matches ZR-PAY-LINK-003
    Section 24 'Agent loses management authority -> Revoke recipient
    authority; suspend payment connection.'"""
    if record.status != "verified":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a verified payment recipient authority can be revoked")
    record.status = "revoked"
    record.verifier_admin_id = revoker.id
    db.commit()
    db.refresh(record)
    return record
