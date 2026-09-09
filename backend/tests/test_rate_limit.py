"""Rate limiting for the chat streaming endpoints.

The in-memory limiter must reject an actor that exceeds the per-window budget
with HTTP 429, while allowing within-window requests. It is keyed per actor so
one user's burst never throttles another.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.rate_limit import RateLimiter, chat_limiter


class TestRateLimiterUnit:
    def test_allows_within_budget(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        assert limiter.allow("a") is True
        assert limiter.allow("a") is True
        assert limiter.allow("a") is True
        assert limiter.allow("a") is False  # 4th exceeds budget

    def test_keys_are_isolated(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        assert limiter.allow("a") is True
        assert limiter.allow("a") is False
        assert limiter.allow("b") is True

    def test_window_resets(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        assert limiter.allow("a") is True
        assert limiter.allow("a") is False
        # Simulate a fresh window.
        import time as _t

        with patch.object(_t, "monotonic", return_value=_t.monotonic() + 120):
            assert limiter.allow("a") is True

    def test_reset(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        assert limiter.allow("a") is True
        assert limiter.allow("a") is False
        limiter.reset("a")
        assert limiter.allow("a") is True


class TestChatEndpointRateLimit:
    """Integration: exceeding the budget on /api/admin/chat and /api/users/chat."""

    def _seed_conversation(self, client, cookies, prefix):
        r = client.post(f"{prefix}/conversations", cookies=cookies)
        return r.json()["id"]

    @patch("app.api.routes.chatbot.stream_assistant_reply")
    def test_admin_stream_rate_limited(self, mock_stream, client, db_session):
        from tests.conftest import _make_admin, auth_admin_cookie

        admin = _make_admin(db_session)
        cookies = auth_admin_cookie(admin)
        conv = self._seed_conversation(client, cookies, "/api/admin/chat")

        def _stream(db, actor, history):
            yield "done", {"blocks": [{"type": "text", "text": "ok"}], "meta": {}}

        mock_stream.side_effect = _stream

        prefix = "/api/admin/chat"
        low = RateLimiter(max_requests=2, window_seconds=60)
        with patch("app.api.routes.chatbot.chat_limiter", low):
            for _ in range(2):
                r = client.post(
                    f"{prefix}/conversations/{conv}/messages/stream",
                    json={"content": "hello"},
                    cookies=cookies,
                )
                assert r.status_code == 200
            r = client.post(
                f"{prefix}/conversations/{conv}/messages/stream",
                json={"content": "hello again"},
                cookies=cookies,
            )
            assert r.status_code == 429

    @patch("app.api.routes.user_chat.stream_assistant_reply")
    def test_user_stream_rate_limited(self, mock_stream, client, db_session):
        from tests.conftest import _make_user, auth_user_cookie

        user = _make_user(db_session)
        cookies = auth_user_cookie(user)
        conv = self._seed_conversation(client, cookies, "/api/users/chat")

        def _stream(db, actor, history):
            yield "done", {"blocks": [{"type": "text", "text": "ok"}], "meta": {}}

        mock_stream.side_effect = _stream

        prefix = "/api/users/chat"
        low = RateLimiter(max_requests=2, window_seconds=60)
        with patch("app.api.routes.user_chat.chat_limiter", low):
            for _ in range(2):
                r = client.post(
                    f"{prefix}/conversations/{conv}/messages/stream",
                    json={"content": "hello"},
                    cookies=cookies,
                )
                assert r.status_code == 200
            r = client.post(
                f"{prefix}/conversations/{conv}/messages/stream",
                json={"content": "hello again"},
                cookies=cookies,
            )
            assert r.status_code == 429

    def test_resets_global_limiter(self):
        chat_limiter.reset()
        assert True
