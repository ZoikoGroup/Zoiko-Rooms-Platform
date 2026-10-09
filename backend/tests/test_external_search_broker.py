"""SRCH-08 -- crawler access-control compliance.

The broker never bypasses authentication, CAPTCHA, paywall or explicit
technical restrictions, and never fetches an un-registered or robots-denied
source. All gates fail closed and are recorded.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.external_search import SourceRightRegistry
from app.services.external_broker import BrokerAccessError, broker


def _add_source(db, **kw):
    defaults = dict(
        source_id="s1",
        source_name_internal="B",
        territories=["GB"],
        acquisition_mode="PUBLIC_FETCH",
        legal_approved=True,
        security_approved=True,
        status="ACTIVE",
        display_permitted=True,
        masking_permitted=True,
        robots_policy="ALLOW_FETCH",
        permitted_fields=["provider_name", "approx_location"],
        cache_ttl_seconds=3600,
    )
    defaults.update(kw)
    db.add(SourceRightRegistry(**defaults))
    db.flush()


class TestAccessControl:
    def test_unregistered_source_blocked(self, db_session):
        with pytest.raises(BrokerAccessError) as exc:
            broker.fetch_allowed(db_session, source_id="ghost")
        assert "not_registered" in str(exc.value)

    def test_registered_source_allowed(self, db_session):
        _add_source(db_session)
        assert broker.fetch_allowed(db_session, source_id="s1") is True

    def test_robots_disallow_fetch_blocked(self, db_session):
        _add_source(db_session, robots_policy="DISALLOW_FETCH")
        with pytest.raises(BrokerAccessError):
            broker.fetch_allowed(db_session, source_id="s1")

    def test_robots_none_blocked(self, db_session):
        _add_source(db_session, robots_policy="NONE")
        with pytest.raises(BrokerAccessError):
            broker.fetch_allowed(db_session, source_id="s1")

    def test_non_active_source_blocked(self, db_session):
        _add_source(db_session, status="SUSPENDED")
        with pytest.raises(BrokerAccessError):
            broker.fetch_allowed(db_session, source_id="s1")

    @pytest.mark.parametrize("flag", ["auth", "captcha", "paywall", "technological_override"])
    def test_no_bypass_ever(self, db_session, flag):
        _add_source(db_session)
        with pytest.raises(BrokerAccessError) as exc:
            broker.fetch_allowed(db_session, source_id="s1", bypass_flags={flag: True})
        assert "bypass_attempted" in str(exc.value)

    def test_robots_status_projection(self, db_session):
        assert broker.robots_status(None) == {"known": False}
        _add_source(db_session, robots_policy="ALLOW_FETCH")
        status = broker.robots_status(
            db_session.scalars(select(SourceRightRegistry)).first()
        )
        assert status["known"] is True
        assert status["robots_policy"] == "ALLOW_FETCH"


class TestBrokerAudited:
    def test_block_recorded_on_chain(self, db_session):
        from sqlalchemy import select

        from app.models.audit import AuditEvent

        with pytest.raises(BrokerAccessError):
            broker.fetch_allowed(db_session, source_id="ghost", correlation_id="c-broker")
        events = db_session.scalars(
            select(AuditEvent).where(AuditEvent.action == "broker.fetch_blocked")
        ).all()
        assert len(events) == 1
        assert events[0].resource_id == "ghost"