"""SRCH-05 for streamed output -- raw model deltas never reach the client.

Contact data split across stream chunks must be masked before any part of it
is released, and the released text must match the fully sanitized reply.
"""

from __future__ import annotations

import pytest

from app.schemas.external_search import ExternalCard, ExternalCardResult, ExternalSearchRestRequest
from app.services.anti_circumvention import StreamSanitizer, sanitizer
from app.services.search_orchestrator import SearchOrchestrator, SearchQuery
from tests.test_qa_scenarios import (
    _Chunk,
    _FakeClient,
    _make_user,
    _new_user_conv,
    _user_stream,
)
from tests.test_search_orchestrator import _seed_listing, _seed_rule

LEAKS = ("alice@example.com", "020 7946 0958", "https://rooms.example.org/l/9", "221 Baker Street")

REPLY = (
    "I found a room that appears listed. The advert says to email alice@example.com "
    "or call 020 7946 0958, see https://rooms.example.org/l/9 or visit 221 Baker Street. "
    "Zoiko Rooms can contact the provider for you instead. " * 2
)


def _chunks(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


@pytest.mark.parametrize("size", [1, 3, 7, 16, 50])
def test_streamed_output_matches_full_sanitization(size):
    guard = StreamSanitizer()
    released = ""
    for part in _chunks(REPLY, size):
        released += guard.feed(part)
        for leak in LEAKS:
            assert leak not in released
            # No partial fragment of a contact value is released either.
            assert leak[:8] not in released
    released += guard.flush()
    assert released == sanitizer.sanitize_text(REPLY)


def test_short_text_is_held_until_flush():
    guard = StreamSanitizer()
    assert guard.feed("call 020 79") == ""
    assert guard.feed("46 0958") == ""
    assert guard.flush() == "call [phone masked]"


def test_user_chat_stream_never_emits_raw_contact_data(client, db_session):
    user = _make_user(db_session)
    conv = _new_user_conv(client, user)
    fake = _FakeClient([
        [_Chunk(text=c) for c in _chunks(REPLY, 5)] + [_Chunk(text="", finish_reason="stop")]
    ])
    res = _user_stream(client, user, conv, "Find me a room in London", fake)
    assert res.status == 200
    streamed = "".join(e["data"]["text"] for e in res.events if e["event"] == "text")
    for leak in LEAKS:
        assert leak not in res.body
    assert streamed == sanitizer.sanitize_text(REPLY)


def test_consumer_card_omits_source_identity():
    card = ExternalCard(source_id="partner_x", source_tier="A", title="[External listing — masked]")
    out = ExternalCardResult.model_validate({**card.model_dump(), "opportunity_id": 7})
    dumped = out.model_dump()
    assert "source_id" not in dumped and "source_tier" not in dumped
    assert "partner_x" not in out.model_dump_json()
    assert dumped["opportunity_id"] == 7


def test_rest_request_rejects_zero_internal_limit():
    with pytest.raises(ValueError):
        ExternalSearchRestRequest(q="room", limit_internal=0)


def test_small_internal_limit_never_opens_external_fallback(db_session):
    """SRCH-01: precedence uses the full qualifying count, not the display cut."""
    _seed_rule(db_session)
    _seed_listing(db_session, slug="listed-1")
    _seed_listing(db_session, slug="listed-2")

    result = SearchOrchestrator().search(
        db_session, SearchQuery(q="en-suite room", city="London", limit_internal=0)
    )
    assert result.discovery.state == "INTERNAL_VERIFIED"
    assert result.discovery.fallback_triggered is False
    assert result.discovery.internal_matches == 2
    assert result.discovery.external_matches == []
