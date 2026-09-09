"""Feature-flag subsystem tests (Phase 7).

Covers:
  * allow-list validation (unknown flags rejected)
  * safe defaults + market/role scoping (England guidance for ENG only)
  * DB override flips effective value, persisted and audit-logged
  * kill switch: disabling a flag gates the corresponding tool family in PDP
  * admin API: list (any admin), update (super_admin only), 422 on unknown/market
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.feature_flag import FeatureFlag
from app.services.chat_service import TOOL_REGISTRY, execute_tool
from app.services.feature_flags import (
    FLAG_REGISTRY,
    FeatureFlagError,
    effective_flags,
    get_flag,
    is_enabled,
    known_flags,
    set_flag,
)
from tests.conftest import (
    _make_admin,
    _make_user,
    auth_admin_cookie,
)

PREFIX = "/api/admin/feature-flags"


# ── Service: registry & defaults ────────────────────────────────────────────


class TestRegistry:
    def test_known_flags_present(self):
        assert "assistant.rag.search_knowledge" in FLAG_REGISTRY
        assert "assistant.stream" in FLAG_REGISTRY
        assert "assistant.handoff" in FLAG_REGISTRY
        assert "assistant.england_launch" in FLAG_REGISTRY

    def test_allow_listed_only(self, db_session: Session):
        assert is_enabled(db_session, "assistant.rag.search_knowledge", role="user")

    def test_unknown_flag_rejected(self, db_session: Session):
        with pytest.raises(FeatureFlagError):
            is_enabled(db_session, "invented.flag")
        with pytest.raises(FeatureFlagError):
            get_flag("invented.flag")

    def test_safe_defaults(self, db_session: Session):
        # England launch is OFF by default, even within its market.
        assert is_enabled(db_session, "assistant.england_launch", market="ENGLAND") is False
        assert is_enabled(db_session, "assistant.england_launch", market="GLOBAL") is False
        assert is_enabled(db_session, "assistant.england_launch") is False

    def test_core_capabilities_default_on(self, db_session: Session):
        assert is_enabled(db_session, "assistant.stream", role="user") is True
        assert is_enabled(db_session, "assistant.handoff", role="user") is True


# ── Service: overrides, scoping, audit ──────────────────────────────────────


class TestOverrides:
    def test_override_flips_effective_value(self, db_session: Session):
        admin = _make_admin(db_session, role="super_admin")
        set_flag(db_session, admin, "assistant.rag.search_knowledge", False, note="kill RAG", role="user")
        assert is_enabled(db_session, "assistant.rag.search_knowledge", role="user") is False
        row = db_session.query(FeatureFlag).filter_by(name="assistant.rag.search_knowledge").first()
        assert row is not None and row.value is False

    def test_override_audited(self, db_session: Session):
        admin = _make_admin(db_session, role="super_admin")
        set_flag(db_session, admin, "assistant.handoff", False, note="temporarily off")
        event = (
            db_session.query(AuditEvent)
            .filter_by(action="feature_flag.updated", resource_id="assistant.handoff")
            .first()
        )
        assert event is not None
        assert admin.id in (event.actor_admin_id,)
        assert "value=False" in event.reason

    def test_market_scope_guard_rejects_wrong_market(self, db_session: Session):
        admin = _make_admin(db_session, role="super_admin")
        with pytest.raises(FeatureFlagError):
            set_flag(db_session, admin, "assistant.england_launch", True, market="GLOBAL")

    def test_market_scoped_flag_effective_only_for_its_market(self, db_session: Session):
        admin = _make_admin(db_session, role="super_admin")
        set_flag(db_session, admin, "assistant.england_launch", True, market="ENGLAND")
        assert is_enabled(db_session, "assistant.england_launch", market="ENGLAND") is True
        # No jurisdiction leakage: GLOBAL (and no-market) stays off.
        assert is_enabled(db_session, "assistant.england_launch", market="GLOBAL") is False
        assert is_enabled(db_session, "assistant.england_launch") is False

    def test_none_db_ignores_overrides_returns_default(self, db_session: Session):
        admin = _make_admin(db_session, role="super_admin")
        set_flag(db_session, admin, "assistant.handoff", False)
        # Without a DB session we fall back to the safe registry default.
        assert is_enabled(None, "assistant.handoff") is True

    def test_effective_flags_covers_all(self, db_session: Session):
        eff = effective_flags(db_session, role="user", market="ENGLAND")
        assert set(eff.keys()) == set(FLAG_REGISTRY.keys())
        assert "assistant.england_launch" in eff


# ── Service: kill switch gates the tool family in PDP ───────────────────────


class TestKillSwitch:
    def test_tool_declares_flag(self):
        assert TOOL_REGISTRY["search_knowledge"].flag == "assistant.rag.search_knowledge"

    def test_disabled_flag_kills_tool(self, db_session: Session):
        admin = _make_admin(db_session, role="super_admin")
        user = _make_user(db_session)
        set_flag(db_session, admin, "assistant.rag.search_knowledge", False, role="user")
        rows, allowed = execute_tool(db_session, user, "search_knowledge", "{}")
        assert allowed is False
        assert any("disabled" in str(v).lower() for v in rows)

    def test_enabled_flag_allows_tool(self, db_session: Session):
        user = _make_user(db_session)
        rows, allowed = execute_tool(db_session, user, "search_knowledge", '{"query":"deposit"}')
        assert allowed is True
        assert isinstance(rows, list)


# ── Admin API ───────────────────────────────────────────────────────────────


class TestFeatureFlagAPI:
    def test_admin_can_list_flags(self, client, db_session: Session):
        admin = _make_admin(db_session)
        r = client.get(PREFIX, cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        names = {f["name"] for f in r.json()["flags"]}
        assert "assistant.england_launch" in names
        assert "assistant.rag.search_knowledge" in names

    def test_super_admin_can_update(self, client, db_session: Session):
        sa = _make_admin(db_session, role="super_admin")
        r = client.put(f"{PREFIX}/assistant.handoff", json={"value": False, "note": "ops"}, cookies=auth_admin_cookie(sa))
        assert r.status_code == 200, r.text
        assert r.json()["value"] is False

    def test_non_super_admin_update_forbidden(self, client, db_session: Session):
        admin = _make_admin(db_session, role="admin")
        r = client.put(f"{PREFIX}/assistant.handoff", json={"value": False}, cookies=auth_admin_cookie(admin))
        assert r.status_code == 403, r.text

    def test_unknown_flag_422(self, client, db_session: Session):
        sa = _make_admin(db_session, role="super_admin")
        r = client.put(f"{PREFIX}/invented.flag", json={"value": True}, cookies=auth_admin_cookie(sa))
        assert r.status_code == 422, r.text

    def test_market_guard_422_on_wrong_market(self, client, db_session: Session):
        sa = _make_admin(db_session, role="super_admin")
        r = client.put(
            f"{PREFIX}/assistant.england_launch",
            json={"value": True, "market": "GLOBAL"},
            cookies=auth_admin_cookie(sa),
        )
        assert r.status_code == 422, r.text
