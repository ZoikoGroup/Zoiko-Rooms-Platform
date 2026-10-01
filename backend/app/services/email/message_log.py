"""Delivery record + deduplication for every email (ZR-COMMS-EMAIL-001 1.3).

Runs in its own short session, independent of the caller's transaction: an
email is a side effect that already happened, so its record must survive
even if the caller later rolls back -- and a logging failure must never stop
the email itself (every function here swallows its own errors).

Tests point _session_factory at their own database (tests/conftest.py)."""

from __future__ import annotations

import hashlib
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Iterator

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.email_message import EmailMessage

logger = logging.getLogger("uvicorn.error")

# A PENDING claim older than this is treated as a crashed send and may be retried.
STALE_PENDING_SECONDS = 15 * 60
DELIVERED = ("SENT", "OUTBOX")


def _default_factory() -> Session | None:
    from app.db.session import _get_session_local

    return _get_session_local()()


# Returns a Session to use, or None to disable logging (e.g. unit tests).
_session_factory: Callable[[], Session | None] = _default_factory


@contextmanager
def _session() -> Iterator[Session | None]:
    db = None
    try:
        db = _session_factory()
        yield db
    finally:
        if db is not None and getattr(db, "_zr_owned_by_log", True):
            db.close()


def content_hash(text_body: str) -> str:
    return hashlib.sha256(text_body.encode("utf-8")).hexdigest()


def claim(
    *, template_id: str, template_version: str, variant: str, tier: int, stream: str, recipient_email: str,
    dedupe_key: str | None, related_entity_type: str = "", related_entity_id: str = "", body_hash: str = "",
) -> tuple[int | None, bool]:
    """Records the message as PENDING. Returns (record_id, should_send).
    should_send is False only when the dedupe key already belongs to a
    delivered (or still-in-flight) message -- that attempt is recorded as
    SUPPRESSED and not sent. A previously FAILED key is reclaimed and retried."""
    try:
        with _session() as db:
            if db is None:
                return None, True
            row = EmailMessage(
                message_id=str(uuid.uuid4()), template_id=template_id, template_version=template_version,
                variant=variant, tier=tier, stream=stream, recipient_email=recipient_email,
                dedupe_key=dedupe_key, related_entity_type=related_entity_type[:50],
                related_entity_id=str(related_entity_id)[:50], status="PENDING", content_hash=body_hash,
            )
            try:
                # Savepoint: a duplicate key undoes only this insert.
                with db.begin_nested():
                    db.add(row)
                db.commit()
                return row.id, True
            except IntegrityError:
                pass

            existing = db.scalar(select(EmailMessage).where(EmailMessage.dedupe_key == dedupe_key))
            if existing is None:  # lost a race with a delete; just send
                return None, True
            if existing.status == "FAILED" or _stale_pending(existing):
                existing.status = "PENDING"
                existing.content_hash = body_hash
                db.commit()
                return existing.id, True

            db.add(EmailMessage(
                message_id=str(uuid.uuid4()), template_id=template_id, template_version=template_version,
                variant=variant, tier=tier, stream=stream, recipient_email=recipient_email,
                dedupe_key=None, duplicate_of_id=existing.id, related_entity_type=related_entity_type[:50],
                related_entity_id=str(related_entity_id)[:50], status="SUPPRESSED", content_hash=body_hash,
            ))
            db.commit()
            return existing.id, False
    except Exception:
        logger.exception("email log: could not record %s for %s -- sending without a record", template_id, recipient_email)
        return None, True


def complete(record_id: int | None, *, status: str, provider: str, attempts: int, error: str = "") -> None:
    if record_id is None:
        return
    try:
        with _session() as db:
            if db is None:
                return
            row = db.get(EmailMessage, record_id)
            if row is None:
                return
            row.status = status
            row.provider = provider
            row.attempts = (row.attempts or 0) + attempts
            row.last_error = error[:500]
            if status in DELIVERED:
                row.sent_at = datetime.now(timezone.utc)
            db.commit()
    except Exception:
        logger.exception("email log: could not complete record %s", record_id)


def _stale_pending(row: EmailMessage) -> bool:
    if row.status != "PENDING" or row.created_at is None:
        return False
    created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds() > STALE_PENDING_SECONDS
