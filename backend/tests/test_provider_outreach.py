"""Phase 1 tests for ZR-AI-SEARCH-001 provider outreach workflow (Section 9).

Covers the 8-step state machine (request -> eligibility -> dispatch ->
acceptance -> controlled introduction -> verification -> internalization),
the Section 9.1 message template invariants, and the background worker
(pending dispatch + TTL expiry). All registry decisions are injected via
``registry._cache`` except where a DB-backed path is being tested.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.external_search import ExternalOpportunity, ProviderOutreach
from app.services.external_outreach import outreach_service
from app.services.outreach_templates import FORBIDDEN_PHRASES, render_provider_outreach
from app.services.outreach_worker import outreach_worker
from app.services.source_rights_registry import registry

from tests.conftest import _make_user

# External discovery and outreach are off until market activation (Section 13).
pytestmark = pytest.mark.usefixtures("external_activated")


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    registry._cache = None
    yield
    registry._cache = None


def _rule(source_id="s1", *, allow_direct_contact=True, channels=None, tier="B") -> dict:
    return {
        "source_id": source_id,
        "domain": f"{source_id}.example.org",
        "tier": tier,
        "allow_fallback": True,
        "allow_direct_contact": allow_direct_contact,
        "allow_indexing": False,
        "policy_ref": "ZR-POL-SRCH-001",
        "outreach_channels": channels or ["EMAIL"],
        "notes": "test rule",
        "is_active": True,
    }


def _seed_opportunity(db, *, source_id="s1", status="EXTERNAL_DISCOVERED",
                      verification_status="NOT_VERIFIED_BY_ZOIKO_ROOMS") -> ExternalOpportunity:
    opp = ExternalOpportunity(
        market_code="GB",
        external_opportunity_id=f"opp-{source_id}",
        source_id=source_id,
        status=status,
        approx_location="Bethnal Green, London",
        advertised_price_currency="GBP",
        advertised_price_minor=90000,
        price_period="MONTH",
        room_type="ensuite",
        verification_status=verification_status,
    )
    db.add(opp)
    db.flush()
    return opp


class TestRequestIntro:
    def test_full_request_flow(self, db_session):
        user = _make_user(db_session)
        opp = _seed_opportunity(db_session)
        registry._cache = {"s1": _rule("s1")}

        po = outreach_service.request_intro(
            db_session,
            user_id=user.id,
            opportunity_id=opp.id,
            consent_fields={"basis": "user_requested_contact", "preference": "email"},
        )
        db_session.flush()

        assert po.outreach_status == "PENDING"
        assert po.channel == "EMAIL"
        assert po.requested_by_user_id == user.id
        assert po.consent_record["basis"] == "user_requested_contact"
        assert opp.status == "OUTREACH_PENDING"

    def test_channel_from_source_rules(self, db_session):
        user = _make_user(db_session)
        opp = _seed_opportunity(db_session)
        registry._cache = {"s1": _rule("s1", channels=["SMS", "EMAIL"])}

        po = outreach_service.request_intro(
            db_session, user_id=user.id, opportunity_id=opp.id
        )
        # The source allows SMS first, but the GB Legal Pack only permits EMAIL
        # and PLATFORM_MESSAGE: the market decides (Section 9.1).
        assert po.channel == "EMAIL"

    def test_unknown_source_blocks(self, db_session):
        user = _make_user(db_session)
        opp = _seed_opportunity(db_session, source_id="ghost")
        registry._cache = {"s1": _rule("s1")}

        with pytest.raises(PermissionError):
            outreach_service.request_intro(
                db_session, user_id=user.id, opportunity_id=opp.id
            )
        assert opp.status == "EXTERNAL_DISCOVERED"

    def test_no_direct_contact_rights_blocks(self, db_session):
        user = _make_user(db_session)
        opp = _seed_opportunity(db_session)
        registry._cache = {"s1": _rule("s1", allow_direct_contact=False)}

        with pytest.raises(PermissionError):
            outreach_service.request_intro(
                db_session, user_id=user.id, opportunity_id=opp.id
            )


class TestSendOutreach:
    def test_dispatch_renders_compliant_message(self, db_session):
        user = _make_user(db_session)
        opp = _seed_opportunity(db_session, status="OUTREACH_PENDING")
        registry._cache = {"s1": _rule("s1")}
        po = outreach_service.request_intro(
            db_session, user_id=user.id, opportunity_id=opp.id
        )
        captured: dict = {}

        def fake_dispatch(_db, _po, body):
            captured["body"] = body
            return "msg-123"

        outreach_service.send_outreach(db_session, po, dispatch=fake_dispatch, now=datetime.now(timezone.utc))

        assert po.outreach_status == "SENT"
        assert po.outreach_sent_at is not None
        body = captured["body"]
        assert body.startswith("Subject: Zoiko Rooms")
        assert "a trading name of Zoiko Realty Group Inc." in body
        assert "/provider/respond?token=" in body          # signed response link
        assert "opt out here" in body and "action=opt-out" in body
        assert render_provider_outreach is not None
        body_lower = captured["body"].lower()
        for phrase in FORBIDDEN_PHRASES:
            assert phrase.lower() not in body_lower
        assert "decline" in body_lower
        assert "listing fee" in body_lower

    def test_suppressed_when_eligibility_revoked(self, db_session):
        user = _make_user(db_session)
        opp = _seed_opportunity(db_session, status="OUTREACH_PENDING")
        registry._cache = {"s1": _rule("s1")}
        po = outreach_service.request_intro(
            db_session, user_id=user.id, opportunity_id=opp.id
        )
        # Source rights change before dispatch -> fail closed.
        registry._cache = {"s1": _rule("s1", allow_direct_contact=False)}

        outreach_service.send_outreach(db_session, po)

        assert po.outreach_status == "SUPPRESSED"
        assert po.outreach_sent_at is None


class TestProviderResponse:
    def test_acceptance_is_not_verification(self, db_session):
        user = _make_user(db_session)
        opp = _seed_opportunity(db_session)
        registry._cache = {"s1": _rule("s1")}
        po = outreach_service.request_intro(
            db_session, user_id=user.id, opportunity_id=opp.id
        )

        outreach_service.handle_provider_response(
            db_session, po, "ACCEPTED", response_detail={"acceptance_ref": "AX-42"}
        )

        assert po.provider_response == "ACCEPTED"
        assert po.provider_response_at is not None
        assert opp.status == "PROVIDER_ACCEPTED"
        assert opp.verification_status == "NOT_VERIFIED_BY_ZOIKO_ROOMS"
        assert opp.intro_unlocked_at is None

    def test_decline_does_not_advance_opportunity(self, db_session):
        user = _make_user(db_session)
        opp = _seed_opportunity(db_session)
        registry._cache = {"s1": _rule("s1")}
        po = outreach_service.request_intro(
            db_session, user_id=user.id, opportunity_id=opp.id
        )

        outreach_service.handle_provider_response(db_session, po, "DECLINED")

        assert po.provider_response == "DECLINED"
        assert opp.status == "OUTREACH_PENDING"

    def test_invalid_response_rejected(self, db_session):
        user = _make_user(db_session)
        opp = _seed_opportunity(db_session)
        registry._cache = {"s1": _rule("s1")}
        po = outreach_service.request_intro(
            db_session, user_id=user.id, opportunity_id=opp.id
        )

        with pytest.raises(ValueError):
            outreach_service.handle_provider_response(db_session, po, "MAYBE")


class TestControlledIntroduction:
    def test_unlock_requires_acceptance(self, db_session):
        opp = _seed_opportunity(db_session, status="OUTREACH_PENDING")
        with pytest.raises(PermissionError):
            outreach_service.unlock_introduction(db_session, opp)

    def test_unlock_after_acceptance(self, db_session):
        opp = _seed_opportunity(db_session, status="PROVIDER_ACCEPTED")
        outreach_service.unlock_introduction(db_session, opp)
        assert opp.intro_unlocked_at is not None
        assert opp.verification_status == "NOT_VERIFIED_BY_ZOIKO_ROOMS"


class TestVerificationAndInternalization:
    def test_start_verification(self, db_session):
        opp = _seed_opportunity(db_session, status="PROVIDER_ACCEPTED")
        outreach_service.start_verification(db_session, opp)
        assert opp.verification_status == "VERIFICATION_IN_PROGRESS"

    def test_internalize_requires_completed_verification(self, db_session):
        opp = _seed_opportunity(db_session, status="PROVIDER_ACCEPTED",
                                verification_status="VERIFICATION_IN_PROGRESS")
        with pytest.raises(PermissionError):
            outreach_service.internalize_listing(db_session, opp)

    def test_internalize_creates_listing(self, db_session):
        opp = _seed_opportunity(
            db_session,
            status="PROVIDER_ACCEPTED",
            verification_status="VERIFIED_AUTHORITY",
        )
        listing = outreach_service.internalize_listing(db_session, opp)
        db_session.flush()

        assert listing.id.startswith("L")
        assert listing.state == "DRAFT"
        assert opp.internal_listing_id == listing.id
        assert opp.status == "INTERNALIZED_VERIFIED"
        assert opp.internal_listing_id is not None

        # Idempotent: second call returns the same listing, no duplicate.
        again = outreach_service.internalize_listing(db_session, opp)
        assert again.id == listing.id


class TestBackgroundWorker:
    def test_process_pending_dispatches_eligible_and_suppresses_blocked(self, db_session):
        user = _make_user(db_session)
        eligible_opp = _seed_opportunity(db_session, source_id="good")
        blocked_opp = _seed_opportunity(db_session, source_id="bad")
        registry._cache = {"good": _rule("good")}

        po_ok = outreach_service.request_intro(
            db_session, user_id=user.id, opportunity_id=eligible_opp.id
        )
        po_bad = ProviderOutreach(
            opportunity_id=blocked_opp.id,
            requested_by_user_id=user.id,
            requested_at=datetime.now(timezone.utc),
            channel="EMAIL",
            outreach_status="PENDING",
            consent_record={},
            audit_trail=[],
        )
        db_session.add(po_bad)
        db_session.flush()

        sent = outreach_worker.process_pending_outreach(db_session, dispatch=lambda _db, _po, _body: "msg-1")

        assert sent == 1
        assert po_ok.outreach_status == "SENT"
        assert po_bad.outreach_status == "SUPPRESSED"

    def test_process_pending_failed_on_exception(self, db_session, monkeypatch):
        user = _make_user(db_session)
        opp = _seed_opportunity(db_session)
        registry._cache = {"s1": _rule("s1")}
        po = outreach_service.request_intro(
            db_session, user_id=user.id, opportunity_id=opp.id
        )

        def boom(_db, _po, body):
            raise RuntimeError("channel down")

        sent = outreach_worker.process_pending_outreach(db_session, dispatch=boom)

        assert sent == 0
        assert po.outreach_status == "FAILED"

    def test_expire_stale_sent(self, db_session):
        user = _make_user(db_session)
        stale_opp = _seed_opportunity(db_session, source_id="stale")
        fresh_opp = _seed_opportunity(db_session, source_id="fresh")
        registry._cache = {"stale": _rule("stale"), "fresh": _rule("fresh")}

        stale = outreach_service.request_intro(
            db_session, user_id=user.id, opportunity_id=stale_opp.id
        )
        fresh = outreach_service.request_intro(
            db_session, user_id=user.id, opportunity_id=fresh_opp.id
        )
        past = datetime.now(timezone.utc) - timedelta(days=30)
        now = datetime.now(timezone.utc)
        stale.outreach_status = "SENT"
        stale.outreach_sent_at = past
        fresh.outreach_status = "SENT"
        fresh.outreach_sent_at = now
        db_session.flush()

        expired = outreach_worker.check_expired_outreach(
            db_session, ttl_days=14, now=datetime.now(timezone.utc)
        )

        assert expired == 1
        assert stale.outreach_status == "EXPIRED"
        assert stale.provider_response == "NO_RESPONSE"
        assert fresh.outreach_status == "SENT"

class TestNoSimulatedDispatch:
    def test_without_channel_adapter_outreach_stays_pending(self, db_session):
        user = _make_user(db_session)
        opp = _seed_opportunity(db_session)
        registry._cache = {"s1": _rule("s1")}
        po = outreach_service.request_intro(db_session, user_id=user.id, opportunity_id=opp.id)

        assert outreach_worker.process_pending_outreach(db_session) == 0
        assert po.outreach_status == "PENDING"
        assert po.outreach_sent_at is None

    def test_outreach_blocked_until_market_activation(self, db_session):
        from app.models.feature_flag import FeatureFlag

        db_session.query(FeatureFlag).filter_by(name="external.provider_outreach").delete()
        db_session.flush()
        user = _make_user(db_session)
        opp = _seed_opportunity(db_session)
        registry._cache = {"s1": _rule("s1")}
        with pytest.raises(PermissionError):
            outreach_service.request_intro(db_session, user_id=user.id, opportunity_id=opp.id)


class TestStaleOpportunityPurge:
    def test_purges_unrequested_past_ttl_and_keeps_requested(self, db_session):
        from datetime import datetime, timedelta, timezone

        from app.models.external_search import ExternalOpportunity

        user = _make_user(db_session)
        registry._cache = {"s1": _rule("s1")}
        old = datetime.now(timezone.utc) - timedelta(days=3)
        stale = _seed_opportunity(db_session, source_id="s2")
        requested = _seed_opportunity(db_session, source_id="s1")
        fresh = _seed_opportunity(db_session, source_id="s3")
        stale.discovered_at = old
        requested.discovered_at = old
        db_session.flush()
        outreach_service.request_intro(db_session, user_id=user.id, opportunity_id=requested.id)
        requested.status = "EXTERNAL_DISCOVERED"  # outreach row alone must protect it
        db_session.flush()
        ids = (stale.id, requested.id, fresh.id)

        assert outreach_worker.purge_stale_opportunities(db_session) == 1
        remaining = {o.id for o in db_session.query(ExternalOpportunity).filter(ExternalOpportunity.id.in_(ids))}
        assert remaining == {requested.id, fresh.id}
