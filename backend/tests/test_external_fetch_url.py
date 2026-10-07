"""SRCH-08 / Section 12 hardening -- SSRF & URL-safety for the fetch broker.

A candidate fetch URL must pass `validate_fetch_url` (allowlisted protocols,
no credentials, no private/loopback/link-local/reserved IPs, no localhost, no
non-standard ports) AND the source-rights gate; any failure raises
BrokerAccessError and is audited.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.services.external_broker import BrokerAccessError, broker, validate_fetch_url


class TestValidateFetchUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.org/room/123",
            "http://flat.example.net/listings",
            "https://example.co.uk/",
        ],
    )
    def test_safe_public_urls_pass(self, url):
        assert validate_fetch_url(url) == (True, "")

    @pytest.mark.parametrize(
        "url, reason",
        [
            ("file:///etc/passwd", "scheme_not_http_https"),
            ("ftp://example.org/x", "scheme_not_http_https"),
            ("http://localhost", "localhost"),
            ("http://localhost:8080/x", "non_standard_port"),
            ("http://127.0.0.1/x", "restricted_ip"),
            ("http://10.0.0.5/x", "restricted_ip"),
            ("http://192.168.1.10/x", "restricted_ip"),
            ("http://[::1]/x", "restricted_ip"),
            ("http://169.254.169.254/latest/meta-data", "restricted_ip"),
            ("https://user:pass@example.org/x", "embedded_credentials"),
            ("https://example.org:8443/x", "non_standard_port"),
            ("not a url at all", "scheme_not_http_https"),
            ("", "empty_url"),
        ],
    )
    def test_unsafe_urls_fail_closed(self, url, reason):
        ok, why = validate_fetch_url(url)
        assert ok is False
        assert reason in why


def _add_source(db, robots="ALLOW_FETCH"):
    from app.models.external_search import SourceRightRegistry

    db.add(
        SourceRightRegistry(
            source_id="s1",
            source_name_internal="B",
            territories=["GB"],
            acquisition_mode="PUBLIC_FETCH",
            legal_approved=True,
            security_approved=True,
            status="ACTIVE",
            display_permitted=True,
            masking_permitted=True,
            robots_policy=robots,
            permitted_fields=["provider_name", "approx_location"],
            cache_ttl_seconds=3600,
        )
    )
    db.flush()


class TestBrokerUrlGate:
    def test_rights_clean_url_allowed(self, db_session):
        _add_source(db_session)
        assert (
            broker.fetch_url_allowed(
                db_session, source_id="s1", url="https://example.org/room/7"
            )
            is True
        )

    def test_metadata_url_blocked_and_audited(self, db_session):
        from app.models.audit import AuditEvent

        _add_source(db_session)
        with pytest.raises(BrokerAccessError) as exc:
            broker.fetch_url_allowed(
                db_session,
                source_id="s1",
                url="http://169.254.169.254/latest/meta-data",
                correlation_id="c-ssrf",
            )
        assert "url_restricted_ip" in str(exc.value)
        events = db_session.scalars(
            select(AuditEvent).where(AuditEvent.action == "broker.fetch_blocked")
        ).all()
        assert len(events) == 1
        assert "url_restricted_ip" in events[0].reason

    def test_scheme_block_blocks(self, db_session):
        from app.models.audit import AuditEvent

        _add_source(db_session)
        with pytest.raises(BrokerAccessError) as exc:
            broker.fetch_url_allowed(
                db_session, source_id="s1", url="file:///etc/hosts", correlation_id="c-file"
            )
        assert "url_scheme_not_http_https" in str(exc.value)
        events = db_session.scalars(
            select(AuditEvent).where(AuditEvent.correlation_id == "c-file")
        ).all()
        assert len(events) >= 1
        assert any("url_scheme_not_http_https" in e.reason for e in events)