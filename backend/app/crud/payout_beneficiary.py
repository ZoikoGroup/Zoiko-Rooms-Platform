"""ZR-ENG-CLR-005 AC-30/Section 9.2: a Party's payout destination is a
verified PayoutBeneficiary, not the Party row itself. Submitting one starts
PENDING_VERIFICATION; confirm_payout_beneficiary strong-auths it with a
mailed one-time code -- the "strongly authenticated" half of AC-30. Only
run_payout (crud/finance.py) reads the resulting VERIFIED row; nothing here
moves money."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.mailer import send_payout_beneficiary_verification_code_email
from app.crud.party import assert_provider_access
from app.crud.user import get_user_by_party_id
from app.models.admin_user import AdminUser
from app.models.finance import PayoutBeneficiary
from app.models.party import Party
from app.schemas.finance import PayoutBeneficiarySubmit
from app.services.bank_identifiers import resolve_bank_identifier_format

VERIFICATION_CODE_EXPIRE_MINUTES = 15
MAX_VERIFICATION_ATTEMPTS = 5


def _hash_code(raw_code: str) -> str:
    return hashlib.sha256(raw_code.encode("utf-8")).hexdigest()


def _generate_and_send_code(db: Session, beneficiary: PayoutBeneficiary, party: Party) -> str:
    """Mints a fresh 6-digit code -- only its hash is persisted, mirroring
    crud/password_reset.py:create_reset_token's own raw-value/hash split --
    and emails it to the party's admin user. Returns the raw code, same as
    create_reset_token, so callers (and tests, exactly like
    test_user_auth_routes.py does for password reset) can exercise the
    confirm step without scraping the email. Sending is best-effort: a
    delivery failure must never block submission/resend, matching every
    other mailer call site in this codebase."""
    raw_code = f"{secrets.randbelow(1_000_000):06d}"
    beneficiary.verification_code_hash = _hash_code(raw_code)
    beneficiary.verification_code_expires_at = datetime.now(timezone.utc) + timedelta(minutes=VERIFICATION_CODE_EXPIRE_MINUTES)
    beneficiary.verification_attempts = 0

    host_user = get_user_by_party_id(db, party.id)
    if host_user:
        send_payout_beneficiary_verification_code_email(
            host_user.email, host_user.full_name, raw_code, VERIFICATION_CODE_EXPIRE_MINUTES,
        )
    return raw_code


def submit_payout_beneficiary(
    db: Session, party: Party, admin: AdminUser, data: PayoutBeneficiarySubmit,
) -> tuple[PayoutBeneficiary, str]:
    """Creates a new PENDING_VERIFICATION beneficiary and sends its
    verification code. Returns (beneficiary, raw_code) -- the route layer
    discards raw_code (PayoutBeneficiaryRead has no such field, so it can
    never reach an API response); it exists so callers never need to scrape
    the sent email. Never persists the full account number -- only its last
    4 digits (AC-33's masking discipline). Multiple PENDING_VERIFICATION rows
    for the same party can coexist (e.g. an abandoned attempt); only VERIFIED
    is uniquely constrained per party."""
    assert_provider_access(db, admin, party.id, roles=("provider_finance", "provider_owner_admin"))

    account_number = data.account_number.strip()
    if not account_number.isdigit() or not (6 <= len(account_number) <= 20):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Account number must be 6-20 digits")

    # ZR-ENG-CLR-005 Section 5's payout data model calls this a generic
    # "beneficiary/account token" -- the routing identifier's shape is a
    # per-country technical fact (app/services/bank_identifiers.py), not one
    # format hard-coded for every provider regardless of jurisdiction.
    bank_format = resolve_bank_identifier_format(party.jurisdiction)
    if bank_format is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"No bank identifier format configured for jurisdiction '{party.jurisdiction}' -- payout beneficiaries "
            "cannot be registered for this jurisdiction yet",
        )
    bank_identifier_code = data.bank_identifier_code.strip().upper()
    if not bank_format.pattern.fullmatch(bank_identifier_code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not a valid {bank_format.label}")
    account_holder_name = data.account_holder_name.strip()
    bank_name = data.bank_name.strip()
    if not account_holder_name or not bank_name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Account holder name and bank name are required")

    beneficiary = PayoutBeneficiary(
        party_id=party.id,
        account_holder_name=account_holder_name,
        bank_name=bank_name,
        account_number_last4=account_number[-4:],
        bank_identifier_code=bank_identifier_code,
    )
    db.add(beneficiary)
    db.flush()
    raw_code = _generate_and_send_code(db, beneficiary, party)
    db.commit()
    db.refresh(beneficiary)
    return beneficiary, raw_code


def resend_payout_beneficiary_code(db: Session, beneficiary: PayoutBeneficiary, admin: AdminUser) -> tuple[PayoutBeneficiary, str]:
    assert_provider_access(db, admin, beneficiary.party_id, roles=("provider_finance", "provider_owner_admin"))
    if beneficiary.status != "PENDING_VERIFICATION":
        raise HTTPException(status.HTTP_409_CONFLICT, "This beneficiary is not awaiting verification")

    party = db.get(Party, beneficiary.party_id)
    raw_code = _generate_and_send_code(db, beneficiary, party)
    db.commit()
    db.refresh(beneficiary)
    return beneficiary, raw_code


def confirm_payout_beneficiary(db: Session, beneficiary: PayoutBeneficiary, admin: AdminUser, raw_code: str) -> PayoutBeneficiary:
    """The strong-auth step itself (AC-30). Wrong code increments the attempt
    counter and is committed immediately, so a brute-force attempt can't be
    reset by retrying after a failed request. Success supersedes whatever
    beneficiary was previously VERIFIED for this party -- a payout account
    change, not an edit in place."""
    assert_provider_access(db, admin, beneficiary.party_id, roles=("provider_finance", "provider_owner_admin"))
    if beneficiary.status != "PENDING_VERIFICATION":
        raise HTTPException(status.HTTP_409_CONFLICT, "This beneficiary is not awaiting verification")
    if not beneficiary.verification_code_expires_at or beneficiary.verification_code_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Verification code has expired -- request a new one")
    if beneficiary.verification_attempts >= MAX_VERIFICATION_ATTEMPTS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many incorrect attempts -- request a new code")

    if _hash_code(raw_code.strip()) != beneficiary.verification_code_hash:
        beneficiary.verification_attempts += 1
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Incorrect verification code")

    previous = db.scalar(
        select(PayoutBeneficiary).where(
            PayoutBeneficiary.party_id == beneficiary.party_id, PayoutBeneficiary.status == "VERIFIED",
        )
    )
    if previous:
        previous.status = "SUPERSEDED"

    beneficiary.status = "VERIFIED"
    beneficiary.verified_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(beneficiary)
    return beneficiary


def get_payout_beneficiary_or_404(db: Session, beneficiary_id: int) -> PayoutBeneficiary:
    beneficiary = db.get(PayoutBeneficiary, beneficiary_id)
    if not beneficiary:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payout beneficiary not found")
    return beneficiary


def list_payout_beneficiaries_for(db: Session, admin: AdminUser, party_id: int) -> list[PayoutBeneficiary]:
    assert_provider_access(db, admin, party_id)
    return list(
        db.scalars(
            select(PayoutBeneficiary).where(PayoutBeneficiary.party_id == party_id).order_by(PayoutBeneficiary.created_at.desc())
        )
    )


def get_verified_payout_beneficiary(db: Session, party_id: int) -> PayoutBeneficiary | None:
    """ZR-ENG-CLR-005 Section 9.2 payout eligibility gate: 'Beneficiary
    identity and payout account are verified to required level.' Read by
    crud/finance.py::run_payout -- a party with no VERIFIED row cannot be
    paid out."""
    return db.scalar(
        select(PayoutBeneficiary).where(PayoutBeneficiary.party_id == party_id, PayoutBeneficiary.status == "VERIFIED")
    )
