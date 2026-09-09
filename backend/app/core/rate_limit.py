"""In-process per-identity rate limiting for LLM-backed endpoints.

Guard against a single authenticated actor exhausting provider budget or
driving unbounded cost through the chat SSE streaming endpoint. This is a
single-node, in-memory sliding count limiter; it is deliberately simple and
stateless across restarts, consistent with the rest of the app (no Redis).

For a horizontally-scaled deployment, replace this with a shared store (Redis
or a DB-backed counter), keeping the same ``RateLimiter.allow(key)`` interface.
"""

from __future__ import annotations

import threading
import time

from app.core.config import settings

# Limits for the chat stream endpoint, sourced from settings so operators can
# tune via env without redeploying.
CHAT_RATE_LIMIT = settings.chat_rate_limit_max
CHAT_RATE_WINDOW = settings.chat_rate_limit_window_seconds


class _Window:
    __slots__ = ("count", "reset_at")

    def __init__(self, reset_at: float) -> None:
        self.count = 0
        self.reset_at = reset_at


class RateLimiter:
    """Fixed-window, per-key request counter. Thread-safe via a lock."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, _Window] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Return True if the key has not exceeded the limit for the current window."""
        now = time.monotonic()
        with self._lock:
            window = self._buckets.get(key)
            if window is None or now >= window.reset_at:
                window = _Window(now + self.window_seconds)
                self._buckets[key] = window
            window.count += 1
            return window.count <= self.max_requests

    def reset(self, key: str | None = None) -> None:
        """Clear a single key (or all keys when None). Primarily for tests."""
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)


# Module-level singleton shared by the chat routes and tests.
chat_limiter = RateLimiter(max_requests=CHAT_RATE_LIMIT, window_seconds=CHAT_RATE_WINDOW)
