"""Scheduled partner feed pulls (ZR-AI-SEARCH-001 Section 6.1 Tier A).

For every PARTNER_FEED source that is ACTIVE, legal + security approved and
has a feed_url, fetch the feed, parse it in its declared format and apply it
as a full snapshot (partner_feeds.sync_snapshot). Each fetch passes the
broker's rights + SSRF gate first, never follows redirects, and is capped in
size. A failing partner never stops the others.
"""

from __future__ import annotations

import logging
import os

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.external_search import SourceRightRegistry
from app.services.audit_ext import log_external_search_event
from app.services.external_broker import BrokerAccessError, broker
from app.services.feed_formats import FeedFormatError, parse_feed
from app.services.partner_feeds import partner_feed_adapter

logger = logging.getLogger(__name__)

MAX_FEED_BYTES = 20 * 1024 * 1024


def _fetch(src: SourceRightRegistry) -> bytes:
    headers = {"Accept": "application/json, text/csv, text/plain, */*"}
    if src.feed_credential_env:
        token = os.environ.get(src.feed_credential_env, "")
        if not token:
            raise PermissionError(f"credential env var {src.feed_credential_env} is not set")
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(timeout=settings.partner_feed_timeout_seconds, follow_redirects=False) as client:
        with client.stream("GET", src.feed_url, headers=headers) as res:
            res.raise_for_status()
            body = b""
            for chunk in res.iter_bytes():
                body += chunk
                if len(body) > MAX_FEED_BYTES:
                    raise ValueError("feed exceeds size limit")
            return body


def sync_partner_feeds(db: Session) -> int:
    """Pull and apply every eligible partner feed. Returns the number of
    feeds applied successfully."""
    sources = db.scalars(
        select(SourceRightRegistry).where(
            SourceRightRegistry.acquisition_mode == "PARTNER_FEED",
            SourceRightRegistry.status == "ACTIVE",
            SourceRightRegistry.legal_approved.is_(True),
            SourceRightRegistry.security_approved.is_(True),
            SourceRightRegistry.feed_url.is_not(None),
        )
    ).all()
    applied = 0
    for src in sources:
        try:
            broker.fetch_url_allowed(db, source_id=src.source_id, url=src.feed_url)
            items = parse_feed(_fetch(src), src.feed_format or "JSON")
            with db.begin_nested():
                partner_feed_adapter.sync_snapshot(db, source_id=src.source_id, items=items)
            db.commit()
            applied += 1
        except (BrokerAccessError, PermissionError, FeedFormatError, httpx.HTTPError, ValueError) as exc:
            db.rollback()
            logger.warning("partner feed %s not applied: %s", src.source_id, exc)
            log_external_search_event(
                db,
                action="partner_feed.sync_failed",
                resource_type="partner_feed",
                resource_id=src.source_id,
                reason=f"{type(exc).__name__}: {str(exc)[:200]}",
            )
            db.commit()
    return applied
