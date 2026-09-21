"""ZR-SUB-003 Section 10: 'Use signed, time-limited URLs for document
access; never expose storage bucket paths.' Previously no route in this
codebase used this pattern -- every file download was a plain authenticated-
session GET. This is the first real, generic implementation: an HMAC-signed
token (reusing settings.jwt_secret under its own namespace, never mixed with
actual JWTs) over (resource_type, resource_id, expiry), verified without any
database round trip. A caller still must also pass the normal ownership/
authority check for the resource -- this only proves the link itself hasn't
expired or been tampered with, the same way a real cloud-storage presigned
URL only proves that and nothing about who's allowed to hold it."""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import HTTPException, status

from app.core.config import settings

DEFAULT_TTL_SECONDS = 15 * 60


def _signature(resource_type: str, resource_id: str, expires_at: int) -> str:
    message = f"{resource_type}:{resource_id}:{expires_at}".encode()
    key = f"signed-url:{settings.jwt_secret}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def generate_signed_download_token(resource_type: str, resource_id: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    expires_at = int(time.time()) + ttl_seconds
    signature = _signature(resource_type, resource_id, expires_at)
    return f"{expires_at}.{signature}"


def verify_signed_download_token(token: str, resource_type: str, resource_id: str) -> None:
    """Raises 403 on a missing/malformed/expired/tampered token -- the route
    calling this still must separately verify the caller may access this
    resource at all (ownership/authority), same as any presigned URL."""
    try:
        expires_at_str, signature = token.split(".", 1)
        expires_at = int(expires_at_str)
    except (ValueError, AttributeError):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid or malformed download link")

    if time.time() > expires_at:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This download link has expired -- request a new one")

    expected = _signature(resource_type, resource_id, expires_at)
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid or malformed download link")
