"""Regression tests for the Source Rights Registry DB load path.

The registry's ``_load`` previously referenced columns that do not exist on
the SourceRightRegistry model (``is_active``, ``domain``, ``tier`` ...), so
any real DB row raised AttributeError and was silently ignored in favour of
the seed file. These tests pin the fail-closed column mapping (_map_db_row)
and prove _load reads ACTIVE/REVIEW rows end-to-end.
"""

from __future__ import annotations

import contextlib

import pytest

from app.models.external_search import SourceRightRegistry
from app.services import source_rights_registry as srr_mod
from app.services.source_rights_registry import registry


@pytest.fixture(autouse=True)
def _reset_cache():
    registry._cache = None
    yield
    registry._cache = None


def _seed_rule(db, *, source_id, status="ACTIVE", display_permitted=False,
               outreach_permitted=False, contact_extraction_permitted=False,
               outreach_channels=None, source_brand_display_rule=None,
               terms_reference=None, approved=True):
    rule = SourceRightRegistry(
        legal_approved=approved,
        security_approved=approved,
        source_id=source_id,
        source_name_internal=f"Source {source_id}",
        territories=["GB"],
        acquisition_mode="LICENSED_API",
        status=status,
        display_permitted=display_permitted,
        outreach_permitted=outreach_permitted,
        contact_extraction_permitted=contact_extraction_permitted,
        outreach_channels=outreach_channels or [],
        source_brand_display_rule=source_brand_display_rule,
        terms_reference=terms_reference,
    )
    db.add(rule)
    db.flush()
    return rule


class TestDBRowMapping:
    def test_display_maps_to_fallback(self):
        # Build an unattached model instance so this test needs no DB query.
        rule = SourceRightRegistry(
            source_id="db_src", source_name_internal="S", territories=[],
            acquisition_mode="LICENSED_API", status="ACTIVE",
            display_permitted=True, outreach_permitted=False,
            contact_extraction_permitted=False,
            legal_approved=True, security_approved=True,
        )
        mapped = srr_mod._map_db_row(rule)
        assert mapped["allow_fallback"] is True
        assert mapped["allow_direct_contact"] is False
        assert mapped["is_active"] is True

    def test_direct_contact_requires_both_permissions(self):
        rule = SourceRightRegistry(
            source_id="db_src", source_name_internal="S", territories=[],
            acquisition_mode="LICENSED_API", status="ACTIVE",
            display_permitted=True, outreach_permitted=True,
            contact_extraction_permitted=False,
        )
        assert srr_mod._map_db_row(rule)["allow_direct_contact"] is False

        rule.contact_extraction_permitted = True
        mapped = srr_mod._map_db_row(rule)
        assert mapped["allow_direct_contact"] is True
        assert mapped["outreach_channels"] == []

    def test_status_drives_tier_and_active(self):
        rule = SourceRightRegistry(
            source_id="db_src", source_name_internal="S", territories=[],
            acquisition_mode="LICENSED_API", status="BLOCKED",
        )
        mapped = srr_mod._map_db_row(rule)
        assert mapped["tier"] == "BLOCKED"
        assert mapped["is_active"] is False

        rule.status = "SUSPENDED"
        assert srr_mod._map_db_row(rule)["is_active"] is False

        # REVIEW is not usable (Section 15.4: status must be ACTIVE).
        rule.status = "REVIEW"
        rule.legal_approved = rule.security_approved = True
        assert srr_mod._map_db_row(rule)["is_active"] is False

    def test_active_requires_legal_and_security_approval(self):
        rule = SourceRightRegistry(
            source_id="db_src", source_name_internal="S", territories=[],
            acquisition_mode="LICENSED_API", status="ACTIVE",
            display_permitted=True, legal_approved=False, security_approved=True,
        )
        assert srr_mod._map_db_row(rule)["is_active"] is False
        rule.legal_approved, rule.security_approved = True, False
        assert srr_mod._map_db_row(rule)["is_active"] is False
        rule.security_approved = True
        assert srr_mod._map_db_row(rule)["is_active"] is True
        rule.acquisition_mode = "BLOCKED"
        assert srr_mod._map_db_row(rule)["is_active"] is False

    def test_contact_channels_and_policy_ref_carried(self):
        rule = SourceRightRegistry(
            source_id="db_src", source_name_internal="S", territories=[],
            acquisition_mode="LICENSED_API", status="ACTIVE",
            display_permitted=True, outreach_permitted=True,
            contact_extraction_permitted=True,
            outreach_channels=["EMAIL", "SMS"],
            source_brand_display_rule="example.org",
            terms_reference="ZR-POL-SRCH-001",
        )
        mapped = srr_mod._map_db_row(rule)
        assert mapped["outreach_channels"] == ["EMAIL", "SMS"]
        assert mapped["domain"] == "example.org"
        assert mapped["policy_ref"] == "ZR-POL-SRCH-001"


class TestLoadEndToEnd:
    def test_db_rows_load_and_unknown_stays_blocked(self, db_session, monkeypatch):
        _seed_rule(
            db_session,
            source_id="db_src",
            display_permitted=True,
            outreach_permitted=True,
            contact_extraction_permitted=True,
            outreach_channels=["EMAIL"],
            source_brand_display_rule="example.org",
        )
        _seed_rule(db_session, source_id="no_contact_src", display_permitted=False)
        db_session.flush()

        @contextlib.contextmanager
        def fake_factory():
            yield db_session

        monkeypatch.setattr(srr_mod, "SessionLocal", fake_factory)
        registry.invalidate()

        rules = registry._load()
        assert rules["db_src"]["allow_fallback"] is True
        assert rules["db_src"]["allow_direct_contact"] is True
        assert rules["db_src"]["outreach_channels"] == ["EMAIL"]
        assert registry.is_direct_contact_allowed("db_src") is True
        assert registry.is_fallback_allowed("db_src") is True
        assert registry.is_direct_contact_allowed("no_contact_src") is False
        assert registry.is_fallback_allowed("no_contact_src") is False
        assert registry.is_direct_contact_allowed("ghost_source") is False

    def test_suspended_rows_are_not_loaded(self, db_session, monkeypatch):
        _seed_rule(
            db_session, source_id="suspended_src", status="SUSPENDED",
            display_permitted=True, outreach_permitted=True,
            contact_extraction_permitted=True,
        )
        _seed_rule(
            db_session, source_id="blocked_src", status="BLOCKED",
            display_permitted=True, outreach_permitted=True,
            contact_extraction_permitted=True,
        )
        db_session.flush()

        @contextlib.contextmanager
        def fake_factory():
            yield db_session

        monkeypatch.setattr(srr_mod, "SessionLocal", fake_factory)
        registry.invalidate()

        rules = registry._load()
        assert "suspended_src" not in rules
        assert "blocked_src" not in rules
        assert registry.is_direct_contact_allowed("suspended_src") is False
        assert registry.is_direct_contact_allowed("blocked_src") is False
    def test_unapproved_and_review_rows_are_not_usable(self, db_session, monkeypatch):
        _seed_rule(
            db_session, source_id="unapproved_src", display_permitted=True,
            outreach_permitted=True, contact_extraction_permitted=True,
            outreach_channels=["EMAIL"], approved=False,
        )
        _seed_rule(
            db_session, source_id="review_src", status="REVIEW",
            display_permitted=True, outreach_permitted=True,
            contact_extraction_permitted=True, outreach_channels=["EMAIL"],
        )
        db_session.flush()

        @contextlib.contextmanager
        def fake_factory():
            yield db_session

        monkeypatch.setattr(srr_mod, "SessionLocal", fake_factory)
        registry.invalidate()

        for sid in ("unapproved_src", "review_src"):
            assert registry.is_fallback_allowed(sid) is False
            assert registry.is_displayable(sid) is False
            assert registry.is_direct_contact_allowed(sid) is False


class TestSeedFallback:
    @staticmethod
    def _empty_db(monkeypatch, db_session):
        @contextlib.contextmanager
        def fake_factory():
            yield db_session

        monkeypatch.setattr(srr_mod, "SessionLocal", fake_factory)
        registry.invalidate()

    def test_seed_is_never_used_in_production(self, db_session, monkeypatch):
        self._empty_db(monkeypatch, db_session)
        monkeypatch.setattr(srr_mod.settings, "environment", "production")
        assert registry._load() == {}
        assert registry.is_displayable("demo_external") is False

    def test_seed_is_used_outside_production(self, db_session, monkeypatch):
        self._empty_db(monkeypatch, db_session)
        monkeypatch.setattr(srr_mod.settings, "environment", "development")
        assert registry.is_displayable("demo_external") is True
        # The seed's "internal" entry sets no is_active flag, so it is closed.
        assert registry.get("internal")["is_active"] is False
