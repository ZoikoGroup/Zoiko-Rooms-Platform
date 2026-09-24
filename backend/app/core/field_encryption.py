"""ZR-PAY-LINK-003 Section 8.1: "Sensitive financial fields are encrypted
and masked in UI/logs." The first at-rest field encryption in this codebase
-- see models/identity_verification.py's own IdentityVerification.
encrypted_reference for the cautionary example this deliberately avoids:
that column is named as if encrypted but never actually was. This one is.

Only for values that must be recoverable in full (the whole point of
RentalPaymentInstruction.encrypted_bank_details -- a tenant has to see the
real account number to pay it). Never use this for a value the existing
"last-4 only, full value never stored" discipline already covers correctly
(see crud/payout_beneficiary.py) -- that discipline is strictly safer and
remains the right choice wherever the full value doesn't actually need to
be read back later."""

from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Fernet:
    return Fernet(settings.field_encryption_key.encode("utf-8"))


def encrypt_json(data: dict[str, str]) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _fernet().encrypt(payload).decode("utf-8")


def decrypt_json(blob: str) -> dict[str, str]:
    """Returns {} for a blank/absent blob (no bank details ever saved for
    this instruction) rather than raising -- same "nothing to decrypt is not
    an error" posture as every other optional-field reader in this
    codebase. A genuinely corrupt/undecryptable blob (wrong key, tampered
    value) does raise InvalidToken -- that must surface, never be silently
    swallowed into an empty dict a caller could mistake for "no details on
    file"."""
    if not blob:
        return {}
    try:
        raw = _fernet().decrypt(blob.encode("utf-8"))
    except InvalidToken:
        raise
    return json.loads(raw.decode("utf-8"))
