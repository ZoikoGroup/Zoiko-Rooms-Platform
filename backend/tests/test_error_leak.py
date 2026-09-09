"""Guard: SSE error events must never leak env vars, stack traces, or provider
internals to the client.

The chat routes log technical detail server-side but surface only hardcoded,
user-safe copy (chatbot.py ERR_* / user_chat.py ERR_*). These tests throw
exceptions seeded with secret-like / implementation-detail strings and assert
nothing sensitive is present in the streamed error payload.
"""

from __future__ import annotations

import json

from unittest.mock import patch


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


def _wedged_error():
    # A deliberately noisy exception carrying implementation detail + a fake
    # secret that must never reach the client.
    return RuntimeError(
        "Traceback (most recent call last):\n"
        "File \"/app/backend/app/services/chat_service.py\", line 700\n"
        "GROQ_API_KEY=gsk-1234567890abcdef SECRET=sekrit provider_timeout=2.5"
    )


class TestAdminStreamNoLeak:
    def test_generic_error_does_not_leak(self, client, db_session):
        from tests.conftest import _make_admin, auth_admin_cookie

        admin = _make_admin(db_session)
        cookies = auth_admin_cookie(admin)
        r = client.post("/api/admin/chat/conversations", cookies=cookies)
        conv = r.json()["id"]

        def _boom(db, actor, history):
            raise _wedged_error()

        with patch("app.api.routes.chatbot.stream_assistant_reply", _boom):
            r = client.post(
                f"/api/admin/chat/conversations/{conv}/messages/stream",
                json={"content": "hi"},
                cookies=cookies,
            )
        assert r.status_code == 200
        events = _parse_sse(r.text)
        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        message = errors[0]["data"]["message"]
        for forbidden in (
            "GROQ_API_KEY",
            "gsk-1234567890abcdef",
            "sekrit",
            "Traceback",
            "chat_service.py",
            "provider_timeout",
            "RuntimeError",
        ):
            assert forbidden not in message, f"leaked {forbidden!r} in {message!r}"


class TestUserStreamNoLeak:
    def test_generic_error_does_not_leak(self, client, db_session):
        from tests.conftest import _make_user, auth_user_cookie

        user = _make_user(db_session)
        cookies = auth_user_cookie(user)
        r = client.post("/api/users/chat/conversations", cookies=cookies)
        conv = r.json()["id"]

        def _boom(db, actor, history):
            raise _wedged_error()

        with patch("app.api.routes.user_chat.stream_assistant_reply", _boom):
            r = client.post(
                f"/api/users/chat/conversations/{conv}/messages/stream",
                json={"content": "hi"},
                cookies=cookies,
            )
        assert r.status_code == 200
        events = _parse_sse(r.text)
        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        message = errors[0]["data"]["message"]
        for forbidden in (
            "GROQ_API_KEY",
            "gsk-1234567890abcdef",
            "sekrit",
            "Traceback",
            "user_chat.py",
            "provider_timeout",
            "RuntimeError",
        ):
            assert forbidden not in message, f"leaked {forbidden!r} in {message!r}"
