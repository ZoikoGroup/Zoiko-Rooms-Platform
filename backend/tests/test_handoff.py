"""Human handoff subsystem tests (Phase 4)."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models.chat import ChatConversation, ChatMessage
from app.models.handoff import AiHandoff
from app.services.handoff import (
    add_bridge_message,
    build_handoff_packet,
    cancel_handoff,
    create_handoff,
    handoff_requested,
)
from tests.conftest import _make_user, auth_user_cookie

PREFIX = "/api/users/chat/handoffs"


def _conversation_with_messages(db: Session, user, *, n: int = 4) -> ChatConversation:
    conv = ChatConversation(user_id=user.id)
    db.add(conv)
    db.flush()
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        content = f"msg {i} about room L12"
        db.add(ChatMessage(conversation_id=conv.id, role=role, content=content))
    db.flush()
    return conv


def _create(client, cookies, conversation_id: int, **overrides) -> dict:
    payload = {"conversationId": conversation_id, "reasonCode": "USER_REQUEST", **overrides}
    r = client.post(PREFIX, json=payload, cookies=cookies)
    assert r.status_code == 201, r.text
    return r.json()


# ── Unit: detector ──────────────────────────────────────────────────────────


class TestHandoffDetector:
    def test_detects_human_request(self):
        assert handoff_requested("can I talk to a human please")
        assert handoff_requested("I want to speak with a real person")
        assert handoff_requested("please connect me with support")

    def test_does_not_trigger_on_normal_questions(self):
        assert not handoff_requested("how much is a room")
        assert not handoff_requested("what listings are near me")


# ── Unit: packet builder ────────────────────────────────────────────────────


class TestPacketBuilder:
    def test_minimum_necessary_excludes_non_text_and_markers(self, db_session: Session):
        user = _make_user(db_session)
        conv = _conversation_with_messages(db_session, user)
        db_session.add(ChatMessage(conversation_id=conv.id, role="assistant", content="your password is secret123"))
        db_session.add(ChatMessage(conversation_id=conv.id, role="assistant", content="call the sell_listing tool with args"))
        db_session.add(ChatMessage(conversation_id=conv.id, role="assistant", content="Here is room L12 details"))
        db_session.flush()

        packet = build_handoff_packet(conv, reason_code="USER_REQUEST", urgency=None, summary=None, request_text="talk to a human")
        manifest = packet.to_manifest()
        # excerpt contains user-safe text
        assert "room L12" in manifest["conversation_excerpt"]
        # secret/password lines are stripped (FRS-HO-002)
        assert "secret123" not in manifest["conversation_excerpt"]
        assert "password" not in manifest["conversation_excerpt"]

    def test_resource_ids_collected(self, db_session: Session):
        user = _make_user(db_session)
        conv = _conversation_with_messages(db_session, user)
        packet = build_handoff_packet(conv, reason_code="USER_REQUEST", urgency=None, summary=None, request_text="")
        assert "L12" in packet.resource_ids

    def test_summary_defaults_from_request_text(self, db_session: Session):
        user = _make_user(db_session)
        conv = _conversation_with_messages(db_session, user)
        packet = build_handoff_packet(conv, reason_code="DISPUTE", urgency=None, summary=None, request_text="I have a dispute")
        assert packet.summary == "I have a dispute"

    def test_urgency_escalates_for_safety(self):
        assert build_handoff_packet(None, reason_code="SAFETY", urgency=None, summary=None, request_text="").urgency == "SAFETY_CRITICAL"
        assert build_handoff_packet(None, reason_code="DISPUTE", urgency=None, summary=None, request_text="").urgency == "HIGH"

    def test_consent_state_for_non_user_request(self, db_session: Session):
        user = _make_user(db_session)
        conv = _conversation_with_messages(db_session, user)
        packet = build_handoff_packet(conv, reason_code="SENSITIVE_COMPLIANCE", urgency=None, summary=None, request_text="")
        assert packet.consent_state == "REVIEW_REQUIRED"


# ── Unit: service ───────────────────────────────────────────────────────────


class TestHandoffService:
    def test_create_and_never_fabricates_case_ref(self, db_session: Session):
        user = _make_user(db_session)
        conv = _conversation_with_messages(db_session, user)
        h = create_handoff(db_session, user, conv, reason_code="USER_REQUEST", request_text="talk to a person")
        stale = db_session.get(AiHandoff, h.id)
        assert stale.status == "REQUESTED"
        assert stale.support_case_ref is None  # FRS-HO-004: never fabricated
        assert stale.reason_code == "USER_REQUEST"

    def test_duplicate_active_handoff_rejected(self, db_session: Session):
        user = _make_user(db_session)
        conv = _conversation_with_messages(db_session, user)
        create_handoff(db_session, user, conv, reason_code="USER_REQUEST", request_text="hi")
        try:
            create_handoff(db_session, user, conv, reason_code="USER_REQUEST", request_text="hi again")
            assert False, "expected ValueError"
        except ValueError:
            pass

    def test_cancel_pending(self, db_session: Session):
        user = _make_user(db_session)
        conv = _conversation_with_messages(db_session, user)
        h = create_handoff(db_session, user, conv, reason_code="USER_REQUEST", request_text="hi")
        cancel_handoff(db_session, user, h)
        stale = db_session.get(AiHandoff, h.id)
        assert stale.status == "CLOSED"

    def test_bridge_message_appended_only_when_pending(self, db_session: Session):
        user = _make_user(db_session)
        conv = _conversation_with_messages(db_session, user)
        h = create_handoff(db_session, user, conv, reason_code="USER_REQUEST", request_text="hi")
        add_bridge_message(db_session, h, "please help")
        stale = db_session.get(AiHandoff, h.id)
        assert json.loads(stale.bridge_messages_json)[0]["text"] == "please help"
        cancel_handoff(db_session, user, h)
        try:
            add_bridge_message(db_session, stale, "again")
            assert False, "expected ValueError"
        except ValueError:
            pass


# ── Integration: routes ─────────────────────────────────────────────────────


class TestHandoffRoutes:
    def test_create_read_cancel_flow(self, client, db_session: Session):
        user = _make_user(db_session)
        cookies = auth_user_cookie(user)
        conv = _conversation_with_messages(db_session, user)
        h = _create(client, cookies, conv.id)
        assert h["status"] == "REQUESTED"
        assert h["supportCaseRef"] is None
        assert "L12" in [str(i) for i in h["contextManifest"]["resource_ids"]]

        r = client.get(f"{PREFIX}/{h['id']}", cookies=cookies)
        assert r.status_code == 200

        r = client.post(f"{PREFIX}/{h['id']}/cancel", cookies=cookies)
        assert r.status_code == 200
        assert r.json()["status"] == "CLOSED"

    def test_create_isolated_between_users(self, client, db_session: Session):
        user_a = _make_user(db_session, email="host_a@test.com")
        user_b = _make_user(db_session, email="host_b@test.com")
        cookies_a = auth_user_cookie(user_a)
        cookies_b = auth_user_cookie(user_b)
        conv = _conversation_with_messages(db_session, user_a)
        h = _create(client, cookies_a, conv.id)
        r = client.get(f"{PREFIX}/{h['id']}", cookies=cookies_b)
        assert r.status_code == 404

    def test_bridge_message_endpoint(self, client, db_session: Session):
        user = _make_user(db_session)
        cookies = auth_user_cookie(user)
        conv = _conversation_with_messages(db_session, user)
        h = _create(client, cookies, conv.id)
        r = client.post(f"{PREFIX}/{h['id']}/messages", json={"text": "urgent please"}, cookies=cookies)
        assert r.status_code == 200
        assert r.json()["bridgeMessages"][0]["text"] == "urgent please"

    def test_invalid_reason_code_rejected(self, client, db_session: Session):
        user = _make_user(db_session)
        cookies = auth_user_cookie(user)
        conv = _conversation_with_messages(db_session, user)
        r = client.post(PREFIX, json={"conversationId": conv.id, "reasonCode": "BOGUS"}, cookies=cookies)
        assert r.status_code == 422

    def test_duplicate_create_returns_409(self, client, db_session: Session):
        user = _make_user(db_session)
        cookies = auth_user_cookie(user)
        conv = _conversation_with_messages(db_session, user)
        _create(client, cookies, conv.id)
        r = client.post(PREFIX, json={"conversationId": conv.id, "reasonCode": "USER_REQUEST"}, cookies=cookies)
        assert r.status_code == 409

    def test_unauthenticated_returns_401(self, client):
        r = client.post(PREFIX, json={"conversationId": 1, "reasonCode": "USER_REQUEST"})
        assert r.status_code == 401
