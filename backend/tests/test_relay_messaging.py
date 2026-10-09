"""Phase 2 tests -- controlled introductions (ZR-AI-SEARCH-001 Section 11).

Relay messaging is only available after a provider-accepted, unlocked
introduction; bodies and sender handles stay masked (contact details are
scrubbed on the way in); direct contact is released only on BILATERAL consent,
and that release is not verification.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.external_search import ExternalOpportunity
from app.services.external_outreach import outreach_service
from app.services.relay_messaging import relay
from app.services.source_rights_registry import registry

from tests.conftest import _make_user

# External discovery and outreach are off until market activation (Section 13).
pytestmark = pytest.mark.usefixtures("external_activated")


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    registry._cache = None
    yield
    registry._cache = None


def _rule(source_id="s1"):
    return {
        "source_id": source_id,
        "domain": "example.org",
        "tier": "B",
        "allow_fallback": True,
        "allow_direct_contact": True,
        "allow_indexing": False,
        "policy_ref": "ZR-POL-SRCH-001",
        "outreach_channels": ["EMAIL"],
        "notes": None,
        "is_active": True,
    }


def _seed_opportunity(db, *, source_id="s1", status="EXTERNAL_DISCOVERED"):
    opp = ExternalOpportunity(
        market_code="GB",
        external_opportunity_id=f"opp-{source_id}",
        source_id=source_id,
        status=status,
        approx_location="Bethnal Green, London",
        verification_status="NOT_VERIFIED_BY_ZOIKO_ROOMS",
    )
    db.add(opp)
    db.flush()
    return opp


def _accepted_and_unlocked(db, user, *, source_id="s1"):
    opp = _seed_opportunity(db, source_id=source_id)
    registry._cache = {source_id: _rule(source_id)}
    po = outreach_service.request_intro(db, user_id=user.id, opportunity_id=opp.id)
    outreach_service.send_outreach(db, po)
    outreach_service.handle_provider_response(db, po, "ACCEPTED")
    opp = db.get(ExternalOpportunity, opp.id)
    outreach_service.unlock_introduction(db, opp)
    return opp


class TestRelayGating:
    def test_messaging_requires_unlocked_introduction(self, db_session):
        user = _make_user(db_session)
        opp = _seed_opportunity(db_session, status="PROVIDER_ACCEPTED")
        with pytest.raises(PermissionError):
            relay.send_relay_message(
                db_session, opportunity_id=opp.id, sender_topic="RENTER", sender_user_id=user.id, content="hi"
            )

    def test_message_sent_after_unlock(self, db_session):
        user = _make_user(db_session)
        opp = _accepted_and_unlocked(db_session, user)

        msg = relay.send_relay_message(
            db_session,
            opportunity_id=opp.id,
            sender_topic="RENTER",
            sender_user_id=user.id,
            content="Hello, is this still advertised?",
        )
        db_session.flush()

        assert msg.sender_handle.startswith("Renter R-")
        assert msg.body == "Hello, is this still advertised?"

    def test_provider_message_has_mask_and_no_user_id(self, db_session):
        user = _make_user(db_session)
        opp = _accepted_and_unlocked(db_session, user)

        msg = relay.send_relay_message(
            db_session, opportunity_id=opp.id, sender_topic="PROVIDER", content="Yes, still available"
        )
        assert msg.sender_handle.startswith("Provider P-")
        assert msg.sender_user_id is None

    def test_renter_message_requires_user(self, db_session):
        user = _make_user(db_session)
        opp = _accepted_and_unlocked(db_session, user)
        with pytest.raises(ValueError):
            relay.send_relay_message(
                db_session, opportunity_id=opp.id, sender_topic="RENTER", content="hi"
            )

    def test_provider_message_cannot_carry_user_id(self, db_session):
        user = _make_user(db_session)
        opp = _accepted_and_unlocked(db_session, user)
        with pytest.raises(ValueError):
            relay.send_relay_message(
                db_session, opportunity_id=opp.id, sender_topic="PROVIDER",
                sender_user_id=user.id, content="hi"
            )

    def test_contact_details_scrubbed_from_bodies(self, db_session):
        user = _make_user(db_session)
        opp = _accepted_and_unlocked(db_session, user)

        msg = relay.send_relay_message(
            db_session,
            opportunity_id=opp.id,
            sender_topic="RENTER",
            sender_user_id=user.id,
            content="Call me at 020 7946 0958 or joe@example.com, address 221 Baker Street",
        )
        assert "[phone masked]" in msg.body
        assert "[email masked]" in msg.body
        assert "[address masked]" in msg.body
        assert "020 7946 0958" not in msg.body

    def test_messages_listed_in_order(self, db_session):
        user = _make_user(db_session)
        opp = _accepted_and_unlocked(db_session, user)
        relay.send_relay_message(db_session, opportunity_id=opp.id, sender_topic="RENTER", sender_user_id=user.id, content="first")
        relay.send_relay_message(db_session, opportunity_id=opp.id, sender_topic="PROVIDER", content="second")

        messages = relay.list_relay_messages(db_session, opp.id)
        assert [m.body for m in messages] == ["first", "second"]


class TestDirectContactRelease:
    def test_no_release_before_both_parties_consent(self, db_session):
        user = _make_user(db_session)
        opp = _accepted_and_unlocked(db_session, user)

        release = relay.request_direct_contact_release(
            db_session, opportunity_id=opp.id, actor_topic="RENTER"
        )
        relay.request_direct_contact_release(
            db_session, opportunity_id=opp.id, actor_topic="PROVIDER"
        )
        db_session.flush()

        assert release.renter_consented_at is not None
        assert release.provider_consented_at is not None
        assert release.both_consented_at is not None
        assert release.released_mask is not None
        assert relay.direct_contact_released(db_session, opp) is True

    def test_release_requires_provider_acceptance(self, db_session):
        user = _make_user(db_session)
        opp = _seed_opportunity(db_session, status="OUTREACH_PENDING")
        registry._cache = {"s1": _rule("s1")}
        outreach_service.request_intro(db_session, user_id=user.id, opportunity_id=opp.id)

        with pytest.raises(PermissionError):
            relay.request_direct_contact_release(
                db_session, opportunity_id=opp.id, actor_topic="RENTER"
            )

    def test_acceptance_alone_is_not_release(self, db_session):
        user = _make_user(db_session)
        opp = _accepted_and_unlocked(db_session, user)
        assert relay.direct_contact_released(db_session, opp) is False


class TestMaskEmail:
    def test_deterministic_and_non_reversible(self):
        a = relay.mask_email("host@example.com")
        b = relay.mask_email("host@example.com")
        c = relay.mask_email("other@example.com")
        assert a == b
        assert a != c
        assert a.endswith("@relay.zoikorooms.com")
        assert "host@example.com" not in a


class TestVerificationJourney:
    def test_start_verification_via_relay(self, db_session):
        user = _make_user(db_session)
        opp = _accepted_and_unlocked(db_session, user)
        out = relay.start_verification_journey(db_session, opp.id)
        assert out.verification_status == "VERIFICATION_IN_PROGRESS"