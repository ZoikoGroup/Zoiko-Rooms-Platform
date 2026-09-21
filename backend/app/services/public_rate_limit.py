"""Shared Postgres-backed rate limiting for anonymous public endpoints.

The existing ``RateLimiter`` (app/core/rate_limit.py) is in-process only: it
works for a single uvicorn worker but lets a determined caller multiply their
budget by however many workers a load balancer fans out to. Public endpoints
therefore use a shared Postgres counter, incremented atomically with an
``INSERT ... ON CONFLICT ... DO UPDATE`` UPSERT keyed on a SHA-256 hash of the
caller identity plus the fixed time window.

Portability: on PostgreSQL the atomic UPSERT path is used unconditionally (the
production driver). On SQLite (the in-memory test fixture) a plain
read/insert-or-update is used instead -- non-atomic, but sufficient for the
single-threaded test harness and never the production path.
"""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.public_rate_limit import PublicRateLimit

# Old windows are pruned lazily; anything older than this is never read again.
_RETENTION_SECONDS = 24 * 60 * 60
_PRUNE_INTERVAL_SECONDS = 60.0

_state_lock = threading.Lock()
_last_prune_at = 0.0


def bucket_key_for(principal: str, window_start: int) -> str:
    """Deterministic bucked key = salted(identity):window_start.

    The identity is hashed so raw IPs (and any other identifying attribute the
    caller passes) are never stored in the table -- only a fixed-length digest.
    """
    digest = hashlib.sha256(principal.encode("utf-8")).hexdigest()
    return f"{digest}:{window_start}"


def _prune_if_due(db: Session) -> None:
    """Delete expired windows at most once per interval (cheap amortized)."""
    global _last_prune_at
    now = time.monotonic()
    with _state_lock:
        if now - _last_prune_at < _PRUNE_INTERVAL_SECONDS:
            return
        _last_prune_at = now
    cutoff = int(time.time()) - _RETENTION_SECONDS
    db.execute(delete(PublicRateLimit).where(PublicRateLimit.window_start < cutoff))


def check_public_rate_limit(
    db: Session,
    *,
    principal: str,
    limit: int,
    window_seconds: int,
) -> bool:
    """Return True iff a call from ``principal`` is allowed in this window.

    ``principal`` is an opaque identity string (the route passes a hashed client
    IP). The counter is incremented for every call, so the first ``limit`` calls
    in a window pass and anything after returns False.
    """
    now = int(time.time())
    window_start = now - (now % window_seconds)
    key = bucket_key_for(principal, window_start)
    _prune_if_due(db)

    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stamp = datetime.now(timezone.utc)
        stmt = (
            pg_insert(PublicRateLimit)
            .values(bucket_key=key, window_start=window_start, count=1, updated_at=stamp)
            .on_conflict_do_update(
                index_elements=[PublicRateLimit.bucket_key],
                set_={"count": PublicRateLimit.count + 1, "updated_at": stamp},
            )
            .returning(PublicRateLimit.count)
        )
        count = db.execute(stmt).scalar_one()
        return bool(count <= limit)

    # SQLite / non-Postgres fallback (test fixtures, local dev without PG).
    row = db.query(PublicRateLimit).filter(PublicRateLimit.bucket_key == key).first()
    if row is None:
        row = PublicRateLimit(bucket_key=key, window_start=window_start, count=1)
        db.add(row)
    else:
        row.count += 1
    db.flush()
    return bool(row.count <= limit)