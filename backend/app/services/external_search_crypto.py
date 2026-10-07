"""At-rest encryption for external source/contact data (ZR-AI-SEARCH-001
Section 12 "Security": encrypt raw source/contact data at rest).

All persisted external contact fields are already named *_encrypted; this
module is what makes that name true, using the existing field_encryption
(Fernet) machinery rather than a second key or store:

* writers use ``encrypt_optional`` before persisting a contact value;
* readers use ``decrypt_contact`` which transparently returns legacy plaintext
  (values written before this module existed) instead of raising, while a
  genuinely corrupt token still surfaces as an error.

Relay masking, partner-feed ingestion and direct-contact release all read and
write through these helpers so no code path can bypass at-rest encryption.
"""

from __future__ import annotations

import base64
import binascii
import logging

logger = logging.getLogger(__name__)

from cryptography.fernet import InvalidToken

from app.core.field_encryption import decrypt_text, encrypt_text


def _looks_like_fernet(blob: str) -> bool:
    """Fernet tokens are urlsafe base64 whose decoded payload opens with the
    version byte 0x80. A value that fails the base64 shape check is legacy
    plaintext (pre-encryption), not a ciphertext."""
    try:
        data = base64.urlsafe_b64decode(blob.encode("ascii"))
    except (ValueError, binascii.Error, UnicodeEncodeError):
        return False
    return bool(data) and data[0] == 0x80


def encrypt_optional(value: str | None) -> str | None:
    """Encrypt a contact value for the *_encrypted columns, or None for a
    blank/absent value (never store a whitespace-only ciphertext)."""
    if value is None or not str(value).strip():
        return None
    return encrypt_text(str(value))


def decrypt_contact(blob: str | None) -> str:
    """Recover the plaintext contact value from an *_encrypted column.

    Returns "" for a blank column. A Fernet token decrypts normally; a value
    that is NOT a Fernet token (legacy plaintext written before at-rest
    encryption existed) is returned unchanged so a read-decision never breaks a
    real record. A Fernet-shaped but tampered token raises InvalidToken -- that
    must surface, never be silently masked into a wrong address.
    """
    if not blob:
        return ""
    if not _looks_like_fernet(blob):
        logger.warning("contact field holds legacy plaintext (pre-encryption); using as-is")
        return blob
    return decrypt_text(blob)