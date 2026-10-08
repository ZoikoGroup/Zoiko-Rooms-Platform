"""SRCH-07 -- licence gate for masked external display.

A source that requires a click-through to the live listing but forbids
masking cannot be shown as a masked external card: masking would suppress the
required click-through and breach the licence. The registry gate must be
fail-closed, including for unknown/inactive/BLOCKED sources.
"""

from __future__ import annotations

import pytest

from app.services.source_rights_registry import registry


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    registry._cache = None
    yield
    registry._cache = None


def _rule(source_id="s1", **overrides):
    rule = {
        "source_id": source_id,
        "domain": "example.org",
        "tier": "B",
        "allow_fallback": True,
        "allow_direct_contact": True,
        "allow_indexing": False,
        "policy_ref": "ZR-POL-SRCH-001",
        "clickthrough_required": False,
        "masking_permitted": True,
        "outreach_channels": ["EMAIL"],
        "notes": None,
        "is_active": True,
    }
    rule.update(overrides)
    return rule


class TestLicenceGate:
    def test_clickthrough_required_without_masking_not_displayable(self):
        registry._cache = {
            "s1": _rule("s1", clickthrough_required=True, masking_permitted=False)
        }
        assert registry.is_displayable("s1") is False

    def test_clickthrough_required_with_masking_displayable(self):
        # Masked card does not suppress the click-through for a masking-permitted
        # source, so the licence is honoured.
        registry._cache = {
            "s1": _rule("s1", clickthrough_required=True, masking_permitted=True)
        }
        assert registry.is_displayable("s1") is True

    def test_masking_forbidden_but_no_clickthrough_required_is_displayable(self):
        registry._cache = {"s1": _rule("s1", masking_permitted=False)}
        assert registry.is_displayable("s1") is True

    def test_unknown_source_fail_closed(self):
        registry._cache = {}  # pragma: no cover - explicit empty seed
        registry._cache = {"s2": _rule("s2")}
        assert registry.is_displayable("s1") is False

    def test_blocked_tier_fail_closed(self):
        registry._cache = {"s1": _rule("s1", tier="BLOCKED")}
        assert registry.is_displayable("s1") is False

    def test_fallback_not_allowed_not_displayable(self):
        registry._cache = {"s1": _rule("s1", allow_fallback=False)}
        assert registry.is_displayable("s1") is False

    def test_masking_rule_survives_db_mapping(self, db_session):
        from app.models.external_search import SourceRightRegistry

        row = SourceRightRegistry(
            source_id="sr1",
            source_name_internal="SR Example",
            territories=["GB"],
            acquisition_mode="PUBLIC_FETCH",
            legal_approved=True,
            security_approved=True,
            status="ACTIVE",
            display_permitted=True,
            outreach_permitted=True,
            contact_extraction_permitted=True,
            masking_permitted=False,
            clickthrough_required=True,
            permitted_fields=["provider_name", "approx_location"],
            cache_ttl_seconds=3600,
            source_brand_display_rule="SR",
        )
        db_session.add(row)
        db_session.flush()

        # DB path (SessionLocal) is unavailable in tests; assert the mapping is
        # faithful so the gate behaves identically when the DB is live.
        from app.services.source_rights_registry import _map_db_row

        mapped = _map_db_row(row)
        assert mapped["clickthrough_required"] is True
        assert mapped["masking_permitted"] is False
        registry._cache = {row.source_id: mapped}
        assert registry.is_displayable(row.source_id) is False