"""External fetch broker -- access-control gate for any crawler/broker step
(ZR-AI-SEARCH-001 SS-2 / SRCH-08).

The broker enforces the access-control invariants BEFORE any fetch: the source
must be a rights-registered source; fetching must be consistent with the
source's robots policy; and the crawler NEVER bypasses authentication,
CAPTCHA, paywalls or other explicit technical restrictions. Every gate is
fail-closed and audited.

Section 12 SSRF/URL safety is enforced here too (``fetch_url_allowed``): only
http/https URLs, no embedded credentials, no literal private/loopback/link-
local/reserved IPs, no localhost, and no non-standard ports. A real fetch layer
must additionally resolve DNS and validate the resolved addresses (and honour
its redirect policy) -- this module is the policy gate the fetch layer has to
pass before making any request.
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.external_search import SourceRightRegistry


class BrokerAccessError(PermissionError):
    """Raised when a fetch would violate the source's access controls."""


def validate_fetch_url(url: str) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for a candidate fetch URL (SRCH-08 hardening).

    Rejects anything that would be an SSRF/egress risk or an access-control
    bypass: non-http(s) schemes, embedded credentials, localhost, literal
    private/loopback/link-local/reserved/unspecified IPs, non-standard ports.
    """
    if not url or not str(url).strip():
        return False, "empty_url"
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return False, "malformed_url"
    if parsed.scheme not in ("http", "https"):
        return False, "scheme_not_http_https"
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "no_host"
    if parsed.username or parsed.password:
        return False, "embedded_credentials"
    if parsed.port is not None and parsed.port not in (80, 443):
        return False, "non_standard_port"
    if host in {"localhost", "localhost."} or host.endswith(".localhost"):
        return False, "localhost"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return False, f"restricted_ip:{ip}"
    return True, ""


class ExternalBroker:
    def fetch_allowed(
        self,
        db: Session,
        *,
        source_id: str,
        bypass_flags: dict[str, bool] | None = None,
        correlation_id: str = "",
    ) -> bool:
        """True only when a fetch is rights-clean; raises BrokerAccessError
        with the specific restriction otherwise."""
        from app.services.audit_ext import log_external_search_event

        src = db.scalar(
            select(SourceRightRegistry).where(SourceRightRegistry.source_id == source_id).limit(1)
        )
        reason = None
        if src is None:
            reason = "source_not_registered"
        elif src.status != "ACTIVE":
            reason = f"source_status_{src.status}"
        elif not (src.legal_approved and src.security_approved):
            reason = "source_not_approved"
        elif str(src.acquisition_mode or "BLOCKED").upper() == "BLOCKED":
            reason = "acquisition_mode_BLOCKED"
        elif src.robots_policy in ("NONE", "DISALLOW_ALL", "DISALLOW_FETCH"):
            reason = f"robots_{src.robots_policy}"

        flags = bypass_flags or {}
        if reason is None and any(flags.get(k) for k in ("auth", "captcha", "paywall", "technological_override")):
            reason = "bypass_attempted"

        if reason is not None:
            log_external_search_event(
                db,
                action="broker.fetch_blocked",
                resource_type="external_fetch",
                resource_id=source_id,
                correlation_id=correlation_id,
                reason=reason,
            )
            raise BrokerAccessError(reason)

        log_external_search_event(
            db,
            action="broker.fetch_allowed",
            resource_type="external_fetch",
            resource_id=source_id,
            correlation_id=correlation_id,
            reason=f"mode={src.acquisition_mode} robots={src.robots_policy or 'UNSPECIFIED'}",
        )
        return True

    def fetch_url_allowed(
        self,
        db: Session,
        *,
        source_id: str,
        url: str,
        bypass_flags: dict[str, bool] | None = None,
        correlation_id: str = "",
    ) -> bool:
        """Rights gate AND SSRF/URL-safety gate for one concrete fetch URL.

        ``fetch_allowed`` first (robots/access controls), then the URL itself.
        Any rejection raises BrokerAccessError and is audited -- the fetch
        layer must treat a raise as 'do not request'."""
        from app.services.audit_ext import log_external_search_event

        self.fetch_allowed(
            db, source_id=source_id, bypass_flags=bypass_flags, correlation_id=correlation_id
        )
        ok, reason = validate_fetch_url(url)
        if not ok:
            log_external_search_event(
                db,
                action="broker.fetch_blocked",
                resource_type="external_fetch",
                resource_id=source_id,
                correlation_id=correlation_id,
                reason=f"url_{reason}",
            )
            raise BrokerAccessError(f"url_{reason}")
        log_external_search_event(
            db,
            action="broker.url_allowed",
            resource_type="external_fetch",
            resource_id=source_id,
            correlation_id=correlation_id,
            reason="fetch_url_safe",
        )
        return True

    def robots_status(self, src: SourceRightRegistry | None) -> dict[str, Any]:
        """Projection of a source's access posture for callers/audit."""
        if src is None:
            return {"known": False}
        return {
            "known": True,
            "robots_policy": src.robots_policy,
            "legal_approved": bool(src.legal_approved),
            "security_approved": bool(src.security_approved),
        }


broker = ExternalBroker()