"""Source Rights Registry -- fail-closed allow/deny per third-party source
(ZR-AI-SEARCH-001 SS-2).

Rules load from the DB (SourceRightRegistry table). Outside production they
fall back to a seed JSON file when the table is empty/unavailable; production
never uses the seed. Unknown sources are BLOCKED.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.external_search import SourceRightRegistry

logger = logging.getLogger(__name__)


BLOCKED = "BLOCKED"

# Registry edits (e.g. suspending a source) must take effect without a
# restart, so the per-process cache is short-lived.
CACHE_TTL_SECONDS = 60


def _map_db_row(row: SourceRightRegistry) -> dict[str, Any]:
    """Map a SourceRightRegistry row to the rule shape every consumer reads
    (mirrors SourceRightEntry). Fail-closed mappings:
      * display = masked external fallback cards
      * direct contact requires BOTH extraction and outreach permission
      * usable only when ACTIVE, legal + security approved and not a BLOCKED
        acquisition mode (Section 15.4)
    """
    return {
        "source_id": row.source_id,
        "domain": row.source_brand_display_rule,
        "tier": str(row.status or BLOCKED).upper(),
        "allow_fallback": bool(row.display_permitted),
        "allow_direct_contact": bool(row.outreach_permitted)
        and bool(row.contact_extraction_permitted),
        "allow_indexing": bool(row.display_permitted),
        "policy_ref": row.terms_reference,
        "clickthrough_required": bool(row.clickthrough_required),
        "masking_permitted": bool(row.masking_permitted),
        "outreach_channels": list(row.outreach_channels or []),
        "notes": None,
        "is_active": row.status == "ACTIVE"
        and bool(row.legal_approved)
        and bool(row.security_approved)
        and str(row.acquisition_mode or BLOCKED).upper() != BLOCKED,
    }


class SourceRightsRegistry:
    def __init__(self) -> None:
        self._rules: dict[str, dict[str, Any]] | None = None
        self._loaded_at = 0.0

    @property
    def _cache(self) -> dict[str, dict[str, Any]] | None:
        return self._rules

    @_cache.setter
    def _cache(self, rules: dict[str, dict[str, Any]] | None) -> None:
        # Any assignment (a DB load, or rules injected directly) starts a new
        # TTL window.
        self._rules = rules
        self._loaded_at = time.monotonic()

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._cache is not None and time.monotonic() - self._loaded_at < CACHE_TTL_SECONDS:
            return self._cache

        rules: dict[str, dict[str, Any]] = {}

        try:
            with SessionLocal() as db:
                # Only ACTIVE sources can ever be used; REVIEW, SUSPENDED and
                # BLOCKED rows are fail-closed and are not loaded at all.
                stmt = select(SourceRightRegistry).where(
                    SourceRightRegistry.status == "ACTIVE"
                )
                for row in db.execute(stmt).scalars():
                    rules[row.source_id] = _map_db_row(row)
        except Exception:  # noqa: BLE001 - registry must be resilient
            logger.warning("SourceRightsRegistry: DB read failed")

        # The seed file is a dev/test convenience only; in production an empty
        # or unreadable registry means every source is blocked.
        if not rules and not settings.is_production:
            for path in (
                Path(__file__).resolve().parents[2] / "data" / "source_rights_registry.json",
                Path(__file__).resolve().parents[0] / "data" / "source_rights_registry.json",
            ):
                if not path.exists():
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    for item in data.get("sources", []):
                        sid = str(item.get("source_id", "")).strip()
                        if not sid:
                            continue
                        rules[sid] = {
                            "source_id": sid,
                            "domain": item.get("domain"),
                            "tier": str(item.get("tier", BLOCKED)).upper(),
                            "allow_fallback": bool(item.get("allow_fallback", False)),
                            "allow_direct_contact": bool(item.get("allow_direct_contact", False)),
                            "allow_indexing": bool(item.get("allow_indexing", False)),
                            "policy_ref": item.get("policy_ref"),
                            "clickthrough_required": bool(item.get("clickthrough_required", False)),
                            "masking_permitted": bool(item.get("masking_permitted", False)),
                            "outreach_channels": list(item.get("outreach_channels", []) or []),
                            "notes": item.get("notes"),
                            "is_active": bool(item.get("is_active", False)),
                        }
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.warning("SourceRightsRegistry seed failed: %s", exc)

        self._cache = rules
        return self._cache

    def is_fallback_allowed(self, source_id: str) -> bool:
        """Fail-closed: unknown or inactive sources never permit fallback."""
        rule = self._load().get(source_id)
        if not rule or not rule.get("is_active", True):
            return False
        if str(rule.get("tier", BLOCKED)).upper() == BLOCKED:
            return False
        return bool(rule.get("allow_fallback", False))

    def is_displayable(self, source_id: str) -> bool:
        """SRCH-07 licence gate: a source that requires a click-through but does
        NOT permit masking cannot be shown as masked external cards (displaying a
        masked card would suppress the required click-through and breach the
        licence). Fail-closed in every other respect."""
        rule = self._load().get(source_id)
        if not rule or not rule.get("is_active", True):
            return False
        if str(rule.get("tier", BLOCKED)).upper() == BLOCKED:
            return False
        if not rule.get("allow_fallback"):
            return False
        if rule.get("clickthrough_required") and not rule.get("masking_permitted"):
            return False
        return True

    def is_direct_contact_allowed(self, source_id: str) -> bool:
        rule = self._load().get(source_id)
        if not rule or not rule.get("is_active", True):
            return False
        if str(rule.get("tier", BLOCKED)).upper() == BLOCKED:
            return False
        return bool(rule.get("allow_direct_contact", False))

    def allow_indexing(self, source_id: str) -> bool:
        rule = self._load().get(source_id)
        if not rule or not rule.get("is_active", True):
            return False
        return bool(rule.get("allow_indexing", False))

    def get(self, source_id: str) -> dict[str, Any] | None:
        return self._load().get(source_id)

    def all_active(self) -> list[dict[str, Any]]:
        return [
            r for r in self._load().values()
            if r.get("is_active", True)
            and str(r.get("tier", BLOCKED)).upper() != BLOCKED
        ]

    def invalidate(self) -> None:
        self._cache = None


registry = SourceRightsRegistry()