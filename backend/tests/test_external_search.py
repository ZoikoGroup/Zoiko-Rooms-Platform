"""Phase 0 tests for ZR-AI-SEARCH-001 (external search protocol).

Covers the blocking QA gates from the implementation plan:
  SRCH-01  internal-first deterministic waterfall
  SRCH-04  masked external cards (no URL/phone/email/address)
  SRCH-05  rights registry fail-closed + no contact without rights
  SRCH-07  anti-circumvention sanitizer
  SRCH-09  consent-required disclosure on external fallback
"""

from __future__ import annotations

import pytest

from app.models.external_search import ExternalOpportunity, ProviderOutreach, SourceRightRegistry
from app.schemas.external_search import ProviderOutreachCreate, SearchState
from app.services.anti_circumvention import sanitizer
from app.services.external_outreach import outreach_service
from app.services.search_orchestrator import (
    EXTERNAL_DISCLOSURE,
    INTERNAL_DISCLOSURE,
    SearchOrchestrator,
    SearchQuery,
)
from app.services.source_rights_registry import registry

from tests.conftest import _make_user

# External discovery and outreach are off until market activation (Section 13).
pytestmark = pytest.mark.usefixtures("external_activated")


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    registry._cache = None
    yield
    registry._cache = None


def _seed_listing(db, *, slug="listed-1", city="London", price=900):
    from app.models.listing import Listing

    listing = Listing(
        id=slug,
        slug=slug,
        name="Bright En-suite Room",
        property_type="private_room",
        room_type="ensuite",
        city=city,
        location="Central London",
        latitude=None,
        longitude=None,
        price_per_night=price,
        currency="GBP",
        state="PUBLISHED",
        guests=1,
        min_stay_nights=30,
        description="A bright ensuite room near the station.",
    )
    db.add(listing)
    db.flush()
    return listing


def _seed_source_rule(db, *, source_id, tier="B", **overrides):
    rule = SourceRightRegistry(
        source_id=source_id,
        source_name_internal=f"Source {source_id}",
        territories=["GB"],
        acquisition_mode="LICENSED_API",
        status="ACTIVE",
        **overrides,
    )
    if "tier" not in overrides:
        # tier isn't a column on the model; stored in registry mapping only.
        pass
    db.add(rule)
    db.flush()
    return rule


def _active_rule_dict(source_id: str, *, tier: str = "B", allow_fallback=True,
                      allow_direct_contact=False) -> dict:
    return {
        "source_id": source_id,
        "domain": f"{source_id}.example.org",
        "tier": tier,
        "allow_fallback": allow_fallback,
        "allow_direct_contact": allow_direct_contact,
        "allow_indexing": False,
        "policy_ref": "ZR-POL-SRCH-001",
        "notes": "test rule",
        "is_active": True,
    }


# ---------------------------------------------------------------------------
# SRCH-01 : internal-first waterfall
# ---------------------------------------------------------------------------


class TestInternalFirstWaterfall:
    def test_internal_match_returns_verified_and_no_external(
        self, db_session
    ):
        _seed_listing(db_session, city="London", price=900)
        registry._cache = {"s1": _active_rule_dict("s1", allow_fallback=True)}

        result = SearchOrchestrator().search(
            db_session, SearchQuery(city="London")
        )
        assert result.discovery.state == SearchState.INTERNAL_VERIFIED
        assert result.discovery.internal_matches == 1
        assert result.discovery.fallback_triggered is False
        assert result.discovery.external_matches == []
        assert len(result.internal_results) == 1
        assert result.internal_results[0]["city"] == "London"

    def test_zero_internal_triggers_eligible_fallback(
        self, db_session
    ):
        _seed_listing(db_session, city="London", price=900)
        registry._cache = {"s1": _active_rule_dict("s1", allow_fallback=True)}

        result = SearchOrchestrator().search(
            db_session, SearchQuery(city="Atlantis", limit_external=5)
        )
        assert result.discovery.state == SearchState.EXTERNAL_DISCOVERED
        assert result.discovery.internal_matches == 0
        assert result.discovery.fallback_triggered is True
        assert result.discovery.consent_required is True
        assert len(result.discovery.external_matches) >= 1

    def test_zero_internal_and_no_eligible_source_blocks(
        self, db_session
    ):
        _seed_listing(db_session, city="London")
        registry._cache = {
            "s1": _active_rule_dict("s1", allow_fallback=False),
        }

        result = SearchOrchestrator().search(
            db_session, SearchQuery(city="Atlantis")
        )
        assert result.discovery.state == SearchState.BLOCKED
        assert result.discovery.fallback_triggered is False
        assert result.discovery.external_matches == []


# ---------------------------------------------------------------------------
# SRCH-04 : masked external cards
# ---------------------------------------------------------------------------


class TestMaskedCards:
    def test_external_cards_expose_no_direct_contact(self, db_session):
        registry._cache = {"s1": _active_rule_dict("s1", allow_fallback=True)}

        result = SearchOrchestrator().search(
            db_session, SearchQuery(city="Atlantis")
        )
        for card in result.discovery.external_matches:
            assert card.has_url is False
            assert card.has_phone is False
            assert card.has_email is False
            assert card.has_exact_address is False
            assert card.is_unlocked is False
            assert card.verification_status == "unverified"
            assert card.canonical_id is None
            assert card.rent_monthly is None

    def test_title_is_sentinel_not_provider_data(self, db_session):
        registry._cache = {"s1": _active_rule_dict("s1", allow_fallback=True)}
        result = SearchOrchestrator().search(
            db_session, SearchQuery(city="Atlantis")
        )
        for card in result.discovery.external_matches:
            assert "masked" in card.title.lower()


# ---------------------------------------------------------------------------
# SRCH-05 : rights registry fail-closed
# ---------------------------------------------------------------------------


class TestSourceRightsRegistryFailClosed:
    def test_unknown_source_is_blocked(self):
        registry._cache = {}
        assert registry.is_fallback_allowed("ghost_source") is False
        assert registry.is_direct_contact_allowed("ghost_source") is False

    def test_blocked_tier_is_disallowed(self):
        registry._cache = {"s1": _active_rule_dict("s1", tier="BLOCKED", allow_fallback=True)}
        assert registry.is_fallback_allowed("s1") is False
        assert registry.is_direct_contact_allowed("s1") is False

    def test_inactive_rule_is_disallowed(self):
        rule = _active_rule_dict("s1", allow_fallback=True)
        rule["is_active"] = False
        registry._cache = {"s1": rule}
        assert registry.is_fallback_allowed("s1") is False

    def test_fallback_flag_required(self):
        registry._cache = {"s1": _active_rule_dict("s1", allow_fallback=False)}
        assert registry.is_fallback_allowed("s1") is False

    def test_direct_contact_requires_explicit_flag(self):
        registry._cache = {"s1": _active_rule_dict("s1", allow_direct_contact=False)}
        assert registry.is_direct_contact_allowed("s1") is False


# ---------------------------------------------------------------------------
# SRCH-07 : anti-circumvention sanitizer
# ---------------------------------------------------------------------------


class TestAntiCircumvention:
    def test_strips_email(self):
        assert sanitizer.sanitize_text("reach me at joe@example.com now") == (
            "reach me at [email masked] now"
        )

    def test_strips_url(self):
        assert sanitizer.sanitize_text("visit https://example.org/room now") == (
            "visit [url masked] now"
        )

    def test_strips_address(self):
        out = sanitizer.sanitize_text("12 Baker Street, London")
        assert "[address masked]" in out

    def test_strips_phone(self):
        out = sanitizer.sanitize_text("call 020 7946 0958 today")
        assert "[phone masked]" in out

    def test_sanitize_card_forces_masked_flags(self):
        card = {
            "title": "Nice room joe@example.com",
            "amenities": ["WiFi", "call 020 7946 0958"],
            "has_url": True,
            "has_phone": True,
            "has_email": True,
            "has_exact_address": True,
            "is_unlocked": True,
        }
        cleaned = sanitizer.sanitize_card(card)
        assert cleaned["has_url"] is False
        assert cleaned["has_phone"] is False
        assert cleaned["has_email"] is False
        assert cleaned["has_exact_address"] is False
        assert cleaned["is_unlocked"] is False
        assert "joe@example.com" not in cleaned["title"]
        assert "joe@example.com" not in " ".join(cleaned["amenities"])


# ---------------------------------------------------------------------------
# SRCH-05 / outreach gate : no contact without direct-contact rights
# ---------------------------------------------------------------------------


class TestOutreachConsentGate:
    def _seed_opportunity(self, db_session, *, source_id="s1", status="EXTERNAL_DISCOVERED"):
        opp = ExternalOpportunity(
            external_opportunity_id="opp-1",
            source_id=source_id,
            status=status,
            approx_location="Atlantis, GB",
            advertised_price_currency="GBP",
            advertised_price_minor=90000,
            price_period="MONTH",
            verification_status="NOT_VERIFIED_BY_ZOIKO_ROOMS",
        )
        db_session.add(opp)
        db_session.flush()
        return opp

    def test_contact_blocked_without_rights(self, db_session):
        user = _make_user(db_session)
        opp = self._seed_opportunity(db_session)
        registry._cache = {"s1": _active_rule_dict("s1", allow_direct_contact=False)}

        with pytest.raises(PermissionError):
            outreach_service.create_outreach(
                db_session,
                ProviderOutreachCreate(
                    opportunity_id=opp.id,
                    requested_by_user_id=user.id,
                ),
            )

    def test_contact_blocked_for_unknown_source(self, db_session):
        user = _make_user(db_session)
        opp = self._seed_opportunity(db_session, source_id="ghost_source")
        registry._cache = {}

        with pytest.raises(PermissionError):
            outreach_service.create_outreach(
                db_session,
                ProviderOutreachCreate(opportunity_id=opp.id, requested_by_user_id=user.id),
            )

    def test_contact_blocked_for_blocked_opportunity(self, db_session):
        user = _make_user(db_session)
        opp = self._seed_opportunity(db_session, status="BLOCKED")
        registry._cache = {"s1": _active_rule_dict("s1", allow_direct_contact=True)}

        with pytest.raises(PermissionError):
            outreach_service.create_outreach(
                db_session,
                ProviderOutreachCreate(opportunity_id=opp.id, requested_by_user_id=user.id),
            )

    def test_contact_denied_when_opportunity_missing(self, db_session):
        user = _make_user(db_session)
        registry._cache = {"s1": _active_rule_dict("s1", allow_direct_contact=True)}

        with pytest.raises(ValueError):
            outreach_service.create_outreach(
                db_session,
                ProviderOutreachCreate(opportunity_id=9999, requested_by_user_id=user.id),
            )

    def test_contact_created_when_rights_permit(self, db_session):
        user = _make_user(db_session)
        opp = self._seed_opportunity(db_session)
        registry._cache = {"s1": _active_rule_dict("s1", allow_direct_contact=True)}

        po = outreach_service.create_outreach(
            db_session,
            ProviderOutreachCreate(
                opportunity_id=opp.id,
                requested_by_user_id=user.id,
                channel="PLATFORM_MESSAGE",
                consent_record={"basis": "user_requested_contact"},
                audit_trail=[{"event": "user_requested_contact"}],
            ),
        )
        assert po.id is not None
        assert po.outreach_status == "PENDING"
        assert po.opportunity_id == opp.id

    def test_outreach_row_records_user_and_consent(self, db_session):
        user = _make_user(db_session)
        opp = self._seed_opportunity(db_session)
        registry._cache = {"s1": _active_rule_dict("s1", allow_direct_contact=True)}

        po = outreach_service.create_outreach(
            db_session,
            ProviderOutreachCreate(
                opportunity_id=opp.id,
                requested_by_user_id=user.id,
                consent_record={"basis": "user_requested_contact", "message": "hello"},
            ),
        )
        db_session.flush()
        assert po.requested_by_user_id == user.id
        assert po.consent_record["basis"] == "user_requested_contact"

    def test_outreach_listing_scoped_to_user(self, db_session):
        user = _make_user(db_session)
        other = _make_user(db_session, email="other@test.com")
        registry._cache = {"s1": _active_rule_dict("s1", allow_direct_contact=True)}
        opp1 = self._seed_opportunity(db_session)
        po = outreach_service.create_outreach(
            db_session,
            ProviderOutreachCreate(opportunity_id=opp1.id, requested_by_user_id=user.id),
        )
        po2 = ProviderOutreach(
            opportunity_id=opp1.id,
            requested_by_user_id=other.id,
            requested_at=po.requested_at,
            channel="PLATFORM_MESSAGE",
            outreach_status="PENDING",
            consent_record={},
            audit_trail=[],
        )
        db_session.add(po2)
        db_session.flush()

        mine = outreach_service.list_outreach_for_user(db_session, user.id)
        assert [p.id for p in mine] == [po.id]


# ---------------------------------------------------------------------------
# Tool registry: new tools exist, registered, role-scoped
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    def test_search_rooms_registered_and_user_scoped(self):
        from app.services.chat_service import TOOL_REGISTRY

        assert "search_rooms" in TOOL_REGISTRY
        assert "request_provider_contact" in TOOL_REGISTRY
        assert "my_external_opportunities" in TOOL_REGISTRY
        assert "external_outreach_queue" in TOOL_REGISTRY
        assert "manage_source_registry" in TOOL_REGISTRY

    def test_user_can_call_my_external_opportunities_empty(self, db_session):
        from tests.conftest import _make_user

        from app.services.chat_service import execute_tool

        user = _make_user(db_session)
        rows, allowed = execute_tool(db_session, user, "my_external_opportunities", "{}")
        assert allowed is True
        assert rows and ("info" in rows[0] or rows[0].get("status"))

    def test_user_listing_returns_rows_for_their_outreach(self, db_session):
        from tests.conftest import _make_user

        from app.models.external_search import ExternalOpportunity
        from app.services.chat_service import execute_tool
        from app.services.external_outreach import outreach_service

        user = _make_user(db_session)
        registry._cache = {"s1": _active_rule_dict("s1", allow_direct_contact=True)}
        opp = ExternalOpportunity(
            external_opportunity_id="opp-user-a",
            source_id="s1",
            status="EXTERNAL_DISCOVERED",
            approx_location="Atlantis, GB",
            advertised_price_currency="GBP",
            advertised_price_minor=90000,
            price_period="MONTH",
            verification_status="NOT_VERIFIED_BY_ZOIKO_ROOMS",
        )
        db_session.add(opp)
        db_session.flush()
        outreach_service.create_outreach(
            db_session,
            ProviderOutreachCreate(opportunity_id=opp.id, requested_by_user_id=user.id),
        )
        db_session.commit()

        rows, allowed = execute_tool(db_session, user, "my_external_opportunities", "{}")
        assert allowed is True
        assert len(rows) == 1
        assert rows[0]["external_opportunity_id"] == "opp-user-a"
        assert rows[0]["masked"] is True
        assert "provider_name" not in rows[0]

    def test_user_cannot_use_admin_outreach_tool(self, db_session):
        from tests.conftest import _make_user

        from app.services.chat_service import execute_tool

        user = _make_user(db_session)
        rows, allowed = execute_tool(db_session, user, "external_outreach_queue", "{}")
        assert allowed is False

    def test_non_superadmin_cannot_manage_registry(self, db_session):
        from tests.conftest import _make_admin

        from app.services.chat_service import execute_tool

        admin = _make_admin(db_session, role="admin")
        rows, allowed = execute_tool(db_session, admin, "manage_source_registry", "{}")
        assert allowed is False

    def test_superadmin_can_view_registry(self, db_session):
        from tests.conftest import _make_admin

        from app.services.chat_service import execute_tool

        sa = _make_admin(db_session, role="super_admin")
        rows, allowed = execute_tool(db_session, sa, "manage_source_registry", "{}")
        assert allowed is True

    def test_admin_can_view_outreach_queue(self, db_session):
        from tests.conftest import _make_admin

        from app.services.chat_service import execute_tool

        admin = _make_admin(db_session)
        rows, allowed = execute_tool(db_session, admin, "external_outreach_queue", "{}")
        assert allowed is True


# ---------------------------------------------------------------------------
# SRCH-09 : disclosure text present on fallback
# ---------------------------------------------------------------------------


class TestDisclosure:
    def test_disclosure_on_external_fallback(self, db_session):
        registry._cache = {"s1": _active_rule_dict("s1", allow_fallback=True)}
        result = SearchOrchestrator().search(db_session, SearchQuery(city="Atlantis"))
        # Section 7.2 mandatory disclosure, verbatim.
        assert result.discovery.disclosure_text == EXTERNAL_DISCLOSURE
        assert "have not been verified by Zoiko Rooms" in result.discovery.disclosure_text

    def test_disclosure_on_internal(self, db_session):
        _seed_listing(db_session, city="London")
        result = SearchOrchestrator().search(db_session, SearchQuery(city="London"))
        # Section 7.1 approved pattern; never claims the inventory is verified.
        assert result.discovery.disclosure_text == INTERNAL_DISCLOSURE
        assert "verified" not in result.discovery.disclosure_text.lower()

    def test_external_search_off_until_market_activation(self, db_session):
        from app.models.feature_flag import FeatureFlag

        db_session.query(FeatureFlag).filter_by(name="external.search_fallback").delete()
        db_session.flush()
        registry._cache = {"s1": _active_rule_dict("s1", allow_fallback=True)}
        result = SearchOrchestrator().search(db_session, SearchQuery(city="Atlantis"))
        assert result.discovery.state == "INTERNAL_ZERO"
        assert result.discovery.external_matches == []
        assert result.discovery.fallback_triggered is False
        assert "EXTERNAL_SEARCH_NOT_ACTIVATED" in result.discovery.guardrail_notes