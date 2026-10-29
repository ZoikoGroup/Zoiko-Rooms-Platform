"""Integration tests for the user chatbot endpoints (/api/users/chat/*)."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

from sqlalchemy.orm import Session

from app.models.chat import ChatConversation
from tests.conftest import (
    _make_user,
    auth_user_cookie,
)

PREFIX = "/api/users/chat"


# ── Helpers ──────────────────────────────────────────────────────────────


def _create_conversation(client, cookies):
    r = client.post(f"{PREFIX}/conversations", cookies=cookies)
    assert r.status_code == 201, r.text
    return r.json()


# ── Conversation CRUD ────────────────────────────────────────────────────


class TestConversationCRUD:
    def test_create_conversation(self, client, db_session: Session):
        user = _make_user(db_session)
        cookies = auth_user_cookie(user)
        conv = _create_conversation(client, cookies)
        assert conv["id"] > 0
        assert conv["title"] == "New conversation"

    def test_list_conversations(self, client, db_session: Session):
        user = _make_user(db_session)
        cookies = auth_user_cookie(user)
        _create_conversation(client, cookies)
        _create_conversation(client, cookies)
        r = client.get(f"{PREFIX}/conversations", cookies=cookies)
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_isolation_between_users(self, client, db_session: Session):
        user_a = _make_user(db_session, email="a@user.com")
        user_b = _make_user(db_session, email="b@user.com")
        cookies_a = auth_user_cookie(user_a)
        cookies_b = auth_user_cookie(user_b)
        conv = _create_conversation(client, cookies_a)
        # user_b must not see user_a's conversation
        r = client.get(f"{PREFIX}/conversations/{conv['id']}/messages", cookies=cookies_b)
        assert r.status_code == 404
        # user_b must not be able to delete user_a's conversation
        r = client.delete(f"{PREFIX}/conversations/{conv['id']}", cookies=cookies_b)
        assert r.status_code == 404

    def test_isolation_from_admin_conversations(self, client, db_session: Session):
        from tests.conftest import _make_admin, auth_admin_cookie

        user = _make_user(db_session)
        admin = _make_admin(db_session)
        # Create admin conversation
        r = client.post("/api/admin/chat/conversations", cookies=auth_admin_cookie(admin))
        admin_conv_id = r.json()["id"]
        # User must not see admin conversation via ID guessing
        r = client.get(f"{PREFIX}/conversations/{admin_conv_id}/messages", cookies=auth_user_cookie(user))
        assert r.status_code == 404

    def test_delete_conversation(self, client, db_session: Session):
        user = _make_user(db_session)
        cookies = auth_user_cookie(user)
        conv = _create_conversation(client, cookies)
        r = client.delete(f"{PREFIX}/conversations/{conv['id']}", cookies=cookies)
        assert r.status_code == 204
        r = client.get(f"{PREFIX}/conversations", cookies=cookies)
        assert len(r.json()) == 0

    def test_list_messages_empty(self, client, db_session: Session):
        user = _make_user(db_session)
        cookies = auth_user_cookie(user)
        conv = _create_conversation(client, cookies)
        r = client.get(f"{PREFIX}/conversations/{conv['id']}/messages", cookies=cookies)
        assert r.status_code == 200
        assert r.json() == []


# ── Auth ──────────────────────────────────────────────────────────────────


class TestAuth:
    def test_unauthenticated_returns_401(self, client):
        r = client.get(f"{PREFIX}/conversations")
        assert r.status_code == 401

    def test_admin_token_cannot_access_user_chat(self, client, db_session: Session):
        from tests.conftest import _make_admin, auth_admin_cookie

        admin = _make_admin(db_session)
        r = client.get(f"{PREFIX}/conversations", cookies=auth_admin_cookie(admin))
        assert r.status_code == 401

    def test_inactive_user_returns_401(self, client, db_session: Session):
        user = _make_user(db_session)
        user.is_active = False
        db_session.flush()
        r = client.get(f"{PREFIX}/conversations", cookies=auth_user_cookie(user))
        assert r.status_code == 401


# ── Message streaming (mocked Groq) ──────────────────────────────────────


class TestMessageStream:
    def _mock_stream(self, text="Here are your rooms"):
        def _stream(db, actor, history):
            yield "text", {"text": text}
            yield "done", {"blocks": [{"type": "text", "text": text}]}
        return _stream

    def test_send_message_streams_response(self, client, db_session: Session):
        user = _make_user(db_session)
        cookies = auth_user_cookie(user)
        conv = _create_conversation(client, cookies)

        with patch("app.api.routes.user_chat.stream_assistant_reply", self._mock_stream()):
            r = client.post(
                f"{PREFIX}/conversations/{conv['id']}/messages/stream",
                json={"content": "Find rooms in Mumbai"},
                cookies=cookies,
            )
            assert r.status_code == 200
            assert "text/event-stream" in r.headers["content-type"]
            events = self._parse_sse(r.text)
            types = [e["event"] for e in events]
            assert "text" in types
            assert "done" in types

    def test_empty_message_returns_422(self, client, db_session: Session):
        user = _make_user(db_session)
        cookies = auth_user_cookie(user)
        conv = _create_conversation(client, cookies)
        r = client.post(
            f"{PREFIX}/conversations/{conv['id']}/messages/stream",
            json={"content": "   "},
            cookies=cookies,
        )
        assert r.status_code == 422

    def test_conversation_title_auto_set(self, client, db_session: Session):
        user = _make_user(db_session)
        cookies = auth_user_cookie(user)
        conv = _create_conversation(client, cookies)

        with patch("app.api.routes.user_chat.stream_assistant_reply", self._mock_stream()):
            client.post(
                f"{PREFIX}/conversations/{conv['id']}/messages/stream",
                json={"content": "Show my payments"},
                cookies=cookies,
            )
        r = client.get(f"{PREFIX}/conversations", cookies=cookies)
        updated = next(c for c in r.json() if c["id"] == conv["id"])
        assert updated["title"] == "Show my payments"

    def test_rate_limit_emits_error_event(self, client, db_session: Session):
        from groq import RateLimitError

        user = _make_user(db_session)
        cookies = auth_user_cookie(user)
        conv = _create_conversation(client, cookies)

        def _raise(db, actor, history):
            raise RateLimitError(
                message="rate limited",
                response=MagicMock(status_code=429, headers={}),
                body=None,
            )

        with patch("app.api.routes.user_chat.stream_assistant_reply", _raise):
            r = client.post(
                f"{PREFIX}/conversations/{conv['id']}/messages/stream",
                json={"content": "test"},
                cookies=cookies,
            )
            events = self._parse_sse(r.text)
            error_events = [e for e in events if e["event"] == "error"]
            assert len(error_events) == 1

    @staticmethod
    def _parse_sse(text: str) -> list[dict]:
        events = []
        for block in text.strip().split("\n\n"):
            event_type = ""
            data_lines = []
            for line in block.split("\n"):
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    data_lines.append(line[6:])
            if event_type:
                data = json.loads("".join(data_lines))
                events.append({"event": event_type, "data": data})
        return events


# ── User tool registry ───────────────────────────────────────────────────


class TestUserToolRegistry:
    def test_user_has_only_user_tools(self, client, db_session: Session):
        from app.services.chat_service import groq_tool_definitions

        user = _make_user(db_session)
        defs = groq_tool_definitions(user)
        names = {d["function"]["name"] for d in defs}
        # Should have user tools
        assert "search_listings" in names
        assert "my_applications" in names
        assert "my_occupancies" in names
        assert "my_obligations" in names
        assert "my_payments" in names
        assert "my_host_listings" in names
        assert "get_listing_details" in names
        # Must NOT have admin tools
        assert "list_bookings" not in names
        assert "list_guests" not in names
        assert "revenue_trend" not in names
        assert "search_platform" not in names


class TestGroqRateLimitRetry:
    """A 429 from Groq is retried after a short wait instead of surfacing
    "Assistant is busy" straight away."""

    @staticmethod
    def _rate_limited(retry_after: str | None = None):
        from groq import RateLimitError

        headers = {"retry-after": retry_after} if retry_after is not None else {}
        return RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429, headers=headers),
            body=None,
        )

    def test_retries_short_rate_limit_then_succeeds(self):
        from app.services import chat_service as cs

        client = MagicMock()
        client.chat.completions.create.side_effect = [self._rate_limited("2"), "ok"]
        with patch.object(cs.time, "sleep") as sleep:
            assert cs.create_completion(client, model="m") == "ok"
        sleep.assert_called_once_with(2.0)
        assert client.chat.completions.create.call_count == 2

    def test_gives_up_after_max_attempts(self):
        from groq import RateLimitError

        from app.services import chat_service as cs

        client = MagicMock()
        client.chat.completions.create.side_effect = [self._rate_limited() for _ in range(5)]
        with patch.object(cs.time, "sleep"):
            try:
                cs.create_completion(client, model="m")
                raise AssertionError("expected RateLimitError")
            except RateLimitError:
                pass
        assert client.chat.completions.create.call_count == cs.RATE_LIMIT_RETRY_ATTEMPTS

    def test_long_retry_after_surfaces_immediately(self):
        from groq import RateLimitError

        from app.services import chat_service as cs

        client = MagicMock()
        client.chat.completions.create.side_effect = [self._rate_limited("600")]
        with patch.object(cs.time, "sleep") as sleep:
            try:
                cs.create_completion(client, model="m")
                raise AssertionError("expected RateLimitError")
            except RateLimitError:
                pass
        sleep.assert_not_called()
