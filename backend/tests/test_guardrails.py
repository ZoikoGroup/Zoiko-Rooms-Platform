"""Deterministic guardrail enforcement (ZR-AI-PG-001 / ZR-AI-ARCH-001).

Covers the pure classifiers (R0-R4, A1-A3, determination scanner) plus the
integration wiring in the chat service: A3 queries are flagged, and
determination-assertion output is refused/rewritten and audited.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.services.guardrails import (
    ActionTier,
    RiskClass,
    classify_action_tier,
    classify_risk,
    risk_topic_name,
    scan_for_determination,
)


# ---------------------------------------------------------------------------
# Pure classifier unit tests
# ---------------------------------------------------------------------------


class TestRiskClassify:
    def test_info(self):
        assert classify_risk("What rooms are available in Mumbai?") == RiskClass.R0

    def test_high_consequence(self):
        assert classify_risk("Do you have any info about the deposit rules?") == RiskClass.R2

    def test_determination(self):
        assert classify_risk("Am I eligible to rent?") == RiskClass.R3

    def test_crisis(self):
        assert classify_risk("I'm in danger, please help") == RiskClass.R4

    def test_blank_is_r0(self):
        assert classify_risk("") == RiskClass.R0


class TestRiskTopic:
    def test_return_hint(self):
        assert risk_topic_name("compliance question") == "compliance"

    def test_no_topic(self):
        assert risk_topic_name("hello there") == ""


class TestActionTier:
    def test_informational_is_a1(self):
        assert classify_action_tier("Show me available rooms in Pune") == ActionTier.A1

    def test_determination_is_a3(self):
        assert classify_action_tier("Am I approved for this application?") == ActionTier.A3

    def test_crisis_is_a3(self):
        assert classify_action_tier("I'm in danger right now") == ActionTier.A3

    def test_compliance_is_a3(self):
        assert classify_action_tier("Is my tenancy compliant?") == ActionTier.A3

    def test_jurisdiction_without_market_is_a2(self):
        assert classify_action_tier("What are the tenant notice period laws?") == ActionTier.A2

    def test_payment_query_is_a1_read(self):
        # Pure account read is not a decision to surface as actionable.
        assert classify_action_tier("Show my payments") == ActionTier.A1


class TestDeterminationScanner:
    def test_clean_output_not_blocked(self):
        assert scan_for_determination("Here are your bookings.").blocked is False

    def test_explicit_approval_blocked(self):
        check = scan_for_determination("Your application is approved.")
        assert check.blocked is True
        assert "approved" in check.matched

    def test_you_are_eligible_blocked(self):
        assert scan_for_determination("You are eligible for this listing.").blocked is True

    def test_you_are_entitled_blocked(self):
        assert scan_for_determination("You are entitled to a full refund.").blocked is True

    def test_blocks_empty(self):
        assert scan_for_determination("").blocked is False


# ---------------------------------------------------------------------------
# Integration wiring: real stream_assistant_reply with a mocked Groq client
# ---------------------------------------------------------------------------


class FakeDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeChoice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class FakeChunk:
    def __init__(self, text=None, finish_reason=None, tool_calls=None):
        choices = [FakeChoice(FakeDelta(content=text, tool_calls=tool_calls), finish_reason=finish_reason)]
        self.choices = choices


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    def create(self, **kwargs):
        return list(self._chunks)


class _FakeChat:
    def __init__(self, chunks):
        self.completions = _FakeCompletions(chunks)


class _FakeClient:
    def __init__(self, chunks):
        self.chat = _FakeChat(chunks)


def _stream_with(text: str):
    """Return a fake Groq client that streams ``text`` then finishes."""
    return _FakeClient([
        FakeChunk(text=text, finish_reason="stop"),
    ])


def _run_service(db, actor, history, client):
    from app.services.chat_service import stream_assistant_reply

    with patch("app.services.chat_service.build_client", return_value=client):
        events = list(stream_assistant_reply(db, actor, history))
    return events


def _user_history(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


def test_a3_query_is_flagged_in_done(monkeypatch):
    from app.models.user_account import UserAccount

    actor = UserAccount(
        email="u@test.com", hashed_password="x", full_name="U", phone="", is_active=True
    )
    client = _stream_with("Here is some info.")
    events = _run_service(None, actor, _user_history("Am I eligible to rent?"), client)
    done = next(e for t, e in events if t == "done")
    assert done["meta"]["action_tier"] == "A3"
    assert done["meta"]["risk"] == "R3"


def test_determination_output_is_noticed(monkeypatch):
    from app.models.user_account import UserAccount

    actor = UserAccount(
        email="u@test.com", hashed_password="x", full_name="U", phone="", is_active=True
    )
    client = _stream_with("Great news: you are approved for this application.")
    events = _run_service(None, actor, _user_history("What is my status?"), client)
    done = next(e for t, e in events if t == "done")
    assert done["meta"]["determination_blocked"] is True
    texts = [b["text"] for b in done["blocks"] if b["type"] == "text"]
    assert any("can't confirm or determine" in t for t in texts)


def test_clean_output_not_blocked(monkeypatch):
    from app.models.user_account import UserAccount

    actor = UserAccount(
        email="u@test.com", hashed_password="x", full_name="U", phone="", is_active=True
    )
    client = _stream_with("Here are 3 available rooms in Pune.")
    events = _run_service(None, actor, _user_history("Show available rooms"), client)
    done = next(e for t, e in events if t == "done")
    assert done["meta"]["determination_blocked"] is False


def test_risk_tier_in_sse_matches_server_classification(client, db_session):
    """End-to-end: the SSE done payload carries the server-computed guardrail."""
    from tests.conftest import _make_user, auth_user_cookie
    from app.services.chat_service import stream_assistant_reply

    user = _make_user(db_session)
    cookies = auth_user_cookie(user)
    r = client.post("/api/users/chat/conversations", cookies=cookies)
    conv = r.json()["id"]

    def mocked_stream(db, actor, history):
        yield "done", {
            "blocks": [{"type": "text", "text": "You are eligible."}],
            "meta": {
                "assistant_surface": "ask_zoiko",
                "system_capability": "zoiko_assist",
                "product": "zoiko_rooms",
                "policy_pack_version": "core",
                "risk": "R3",
                "risk_topic": "eligibility",
                "action_tier": "A3",
                "determination_blocked": True,
            },
        }

    with patch("app.api.routes.user_chat.stream_assistant_reply", mocked_stream):
        r = client.post(
            f"/api/users/chat/conversations/{conv}/messages/stream",
            json={"content": "Am I eligible?"},
            cookies=cookies,
        )
    assert r.status_code == 200
    events = []
    for block in r.text.strip().split("\n\n"):
        et, dl = "", []
        for line in block.split("\n"):
            if line.startswith("event: "):
                et = line[7:]
            elif line.startswith("data: "):
                dl.append(line[6:])
        if et:
            events.append((et, json.loads("".join(dl))))
    done = next(v for k, v in events if k == "done")
    assert done["guardrail"]["risk"] == "R3"
    assert done["guardrail"]["action_tier"] == "A3"
    assert done["guardrail"]["determination_blocked"] is True
