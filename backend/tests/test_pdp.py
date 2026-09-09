"""Policy Decision Point (PDP) unit + integration tests.

Covers:
  * RBAC: role gating (who may invoke which tool family)
  * ABAC: ownership / state-based guard for listing reads
  * Cross-account denial: non-super admin cannot read another admin's listing
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.admin_user import AdminUser
from app.models.listing import Listing
from app.models.user_account import UserAccount
from app.services.chat_service import TOOL_REGISTRY
from app.services.pdp import (
    Decision,
    check_permission,
)

# ── Fixtures ────────────────────────────────────────────────────────────────

SQLITE_URL = "sqlite:///:memory:"


@pytest.fixture()
def db():
    eng = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _pragma(dbapi, _):
        dbapi.cursor().execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    conn = eng.connect()
    tx = conn.begin()
    session = Session(bind=conn)
    yield session
    session.close()
    tx.rollback()
    conn.close()
    eng.dispose()


def _admin(db: Session, *, id: int = 1, role: str = "admin") -> AdminUser:
    admin = AdminUser(
        id=id,
        email=f"admin{id}@test.com",
        hashed_password="x",
        full_name=f"Admin {id}",
        role=role,
        is_active=True,
        approval_status="approved",
    )
    db.add(admin)
    db.flush()
    return admin


def _user(db: Session) -> UserAccount:
    user = UserAccount(
        email="user@test.com",
        hashed_password="x",
        full_name="User",
        is_active=True,
        email_verified=True,
    )
    db.add(user)
    db.flush()
    return user


def _listing(db: Session, *, id: str = "L1", owner_id: int | None = None, state: str = "PUBLISHED") -> Listing:
    listing = Listing(
        id=id,
        slug=f"listing-{id.lower()}",
        name=f"Room {id}",
        room_type="single_occupancy",
        city="Mumbai",
        location="Andheri West",
        price_per_night=1500,
        currency="INR",
        guests=1,
        bedrooms=1,
        bathrooms=1,
        state=state,
        owner_id=owner_id,
    )
    db.add(listing)
    db.flush()
    return listing


def _tool(name: str):
    return TOOL_REGISTRY[name]


# ── RBAC tests ──────────────────────────────────────────────────────────────


class TestRBAC:
    def test_user_denied_admin_tool(self, db: Session):
        actor = _user(db)
        decision = check_permission(actor, _tool("search_platform"), {}, db)
        assert decision.result == Decision.DENY
        assert decision.reason_code == "AUTH_SCOPE_MISMATCH"

    def test_admin_denied_user_tool(self, db: Session):
        actor = _admin(db)
        decision = check_permission(actor, _tool("search_listings"), {}, db)
        assert decision.result == Decision.DENY
        assert decision.reason_code == "AUTH_SCOPE_MISMATCH"

    def test_non_super_admin_denied_super_admin_only(self, db: Session):
        actor = _admin(db, role="admin")
        decision = check_permission(actor, _tool("list_bookings"), {}, db)
        assert decision.result == Decision.DENY
        assert decision.reason_code == "AUTH_SCOPE_MISMATCH"

    def test_super_admin_permit_super_admin_only(self, db: Session):
        actor = _admin(db, id=99, role="super_admin")
        decision = check_permission(actor, _tool("list_bookings"), {}, db)
        assert decision.result == Decision.PERMIT

    def test_tool_without_permission_skips_abac_guard(self, db: Session):
        actor = _admin(db)
        decision = check_permission(actor, _tool("search_platform"), {}, db)
        assert decision.result == Decision.PERMIT

    def test_unknown_tool_name_raises_key_error(self, db: Session):
        actor = _admin(db)
        with pytest.raises(KeyError):
            check_permission(actor, _tool("does_not_exist"), {}, db)


# ── ABAC: ownership guard (listing.detail) ──────────────────────────────────


class TestABACListingDetail:
    def test_owner_permitted(self, db: Session):
        admin = _admin(db, id=1)
        _listing(db, id="L1", owner_id=1)
        decision = check_permission(admin, _tool("get_listing"), {"listing_id": "L1"}, db)
        assert decision.result == Decision.PERMIT

    def test_non_owner_denied(self, db: Session):
        admin_a = _admin(db, id=1, role="admin")
        admin_b = _admin(db, id=2, role="admin")
        _listing(db, id="L2", owner_id=2)
        decision = check_permission(admin_a, _tool("get_listing"), {"listing_id": "L2"}, db)
        assert decision.result == Decision.DENY
        assert decision.reason_code == "AUTH_OBJECT_RELATIONSHIP_MISSING"

    def test_super_admin_permitted_for_any_listing(self, db: Session):
        super_admin = _admin(db, id=99, role="super_admin")
        _admin(db, id=1)
        _listing(db, id="L3", owner_id=1)
        decision = check_permission(super_admin, _tool("get_listing"), {"listing_id": "L3"}, db)
        assert decision.result == Decision.PERMIT

    def test_non_existent_listing_denied(self, db: Session):
        admin = _admin(db, id=1)
        decision = check_permission(admin, _tool("get_listing"), {"listing_id": "NOPE"}, db)
        assert decision.result == Decision.DENY
        assert decision.reason_code == "AUTH_RESOURCE_NOT_FOUND"

    def test_missing_listing_id_denied(self, db: Session):
        admin = _admin(db, id=1)
        decision = check_permission(admin, _tool("get_listing"), {}, db)
        assert decision.result == Decision.DENY
        assert decision.reason_code == "AUTH_RESOURCE_NOT_FOUND"


# ── ABAC: state guard (listing.read_published) ──────────────────────────────


class TestABACListingReadPublished:
    def test_published_listing_permitted(self, db: Session):
        actor = _user(db)
        _listing(db, id="U1", state="PUBLISHED")
        decision = check_permission(actor, _tool("get_listing_details"), {"listing_id": "U1"}, db)
        assert decision.result == Decision.PERMIT

    def test_draft_listing_denied(self, db: Session):
        actor = _user(db)
        _listing(db, id="U2", state="DRAFT")
        decision = check_permission(actor, _tool("get_listing_details"), {"listing_id": "U2"}, db)
        assert decision.result == Decision.DENY
        assert decision.reason_code == "AUTH_PROPERTY_DENIED"

    def test_paused_listing_denied(self, db: Session):
        actor = _user(db)
        _listing(db, id="U3", state="PAUSED")
        decision = check_permission(actor, _tool("get_listing_details"), {"listing_id": "U3"}, db)
        assert decision.result == Decision.DENY
        assert decision.reason_code == "AUTH_PROPERTY_DENIED"

    def test_non_existent_listing_denied(self, db: Session):
        actor = _user(db)
        decision = check_permission(actor, _tool("get_listing_details"), {"listing_id": "NOPE"}, db)
        assert decision.result == Decision.DENY
        assert decision.reason_code == "AUTH_RESOURCE_NOT_FOUND"


# ── execute_tool integration (PDP wired into execute_tool) ──────────────────


class TestExecuteToolPDP:
    def test_execute_tool_denies_cross_account(self, db: Session):
        from app.services.chat_service import execute_tool
        import json

        admin_a = _admin(db, id=1, role="admin")
        _admin(db, id=2)
        _listing(db, id="L10", owner_id=2)
        rows, allowed = execute_tool(db, admin_a, "get_listing", json.dumps({"listing_id": "L10"}))
        assert allowed is False
        assert "AUTH_OBJECT_RELATIONSHIP_MISSING" in rows[0]["error"]

    def test_execute_tool_permits_owner(self, db: Session):
        from app.services.chat_service import execute_tool
        import json

        admin_a = _admin(db, id=1, role="admin")
        _listing(db, id="L11", owner_id=1, state="DRAFT")
        rows, allowed = execute_tool(db, admin_a, "get_listing", json.dumps({"listing_id": "L11"}))
        assert allowed is True

    def test_execute_tool_user_draft_listing_denied(self, db: Session):
        from app.services.chat_service import execute_tool
        import json

        actor = _user(db)
        _listing(db, id="L20", state="DRAFT")
        rows, allowed = execute_tool(db, actor, "get_listing_details", json.dumps({"listing_id": "L20"}))
        assert allowed is False
        assert "AUTH_PROPERTY_DENIED" in rows[0]["error"]

    def test_execute_tool_user_published_listing_permitted(self, db: Session):
        from app.services.chat_service import execute_tool
        import json

        actor = _user(db)
        _listing(db, id="L21", state="PUBLISHED")
        rows, allowed = execute_tool(db, actor, "get_listing_details", json.dumps({"listing_id": "L21"}))
        assert allowed is True
