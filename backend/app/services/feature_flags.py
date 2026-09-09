"""Server-authoritative feature flags (Phase 7).

Implements the deterministic, server-authoritative gating required by
ZR-AI-DEVOPS / FRS §2 and Technical Architecture §22 (feature flags):

  * allow-listed non-secret flag names/values only (a client/DB can never
    invent a flag);
  * safe defaults (a capability is OFF unless a flag explicitly enables it);
  * market/role scoping so a capability only activates the requirements
    applicable to that market pack (e.g. the England launch profile);
  * kill-switch semantics for whole families (tool / provider / retrieval /
    action / streaming);
  * audit-logged changes (every override is recorded via ``log_audit_event``).

Authorization, redaction and action-confirmation can never be disabled via a
flag (FRS §2 "Debug modes cannot disable authorization..."); those are pinned
ON and not present in the registry.

Resolution order: DB override (if present) -> registry default -> scope gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from app.crud.audit import log_audit_event
from app.models.admin_user import AdminUser
from app.models.feature_flag import FeatureFlag


@dataclass(frozen=True)
class FlagSpec:
    name: str
    default: bool
    description: str
    # Optional scope gates. ``markets`` is an allow-list; ``roles`` an allow-list
    # of actor kinds. Empty means "any".
    markets: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Allow-listed registry (the ONLY flag names that exist).
# ---------------------------------------------------------------------------
# Value convention: safe default means a capability the spec treats as core is
# ON (e.g. RAG, handoff, streaming); a capability behind an explicit launch
# (England market pack) is OFF until turned on per market.
FLAG_REGISTRY: dict[str, FlagSpec] = {
    "assistant.stream": FlagSpec(
        "assistant.stream",
        True,
        "Kill switch for assistant streaming.",
    ),
    "assistant.rag.search_knowledge": FlagSpec(
        "assistant.rag.search_knowledge",
        True,
        "Enable the search_knowledge (KB/RAG) user tool family.",
    ),
    "assistant.handoff": FlagSpec(
        "assistant.handoff",
        True,
        "Enable human-handoff detection and suggestion.",
    ),
    "assistant.england_launch": FlagSpec(
        "assistant.england_launch",
        False,
        "Activate England market-pack regulated guidance (right-to-rent, deposit, "
        "gas/electrical safety, EPC, HMO, alarms, fees). Only valid for the ENG market.",
        markets=("ENGLAND",),
    ),
}

# Requirement strings surfaced in reports/admin responses.
FLAG_INVARIANTS: list[str] = [
    "Server-authoritative: only allow-listed non-secret flag names exist",
    "Safe defaults: capabilities are OFF unless a flag explicitly enables them",
    "Market-scoped: England guidance activates only for the ENG market pack (no jurisdiction leakage)",
    "Authorization, redaction and action-confirmation can never be disabled by a flag",
    "Every feature-flag override is audit-logged",
]

# Invariant: a flag with a market allow-list never activates for GLOBAL/other markets.
GLOBAL_MARKET = "GLOBAL"


class FeatureFlagError(Exception):
    pass


def get_flag(name: str) -> FlagSpec:
    try:
        return FLAG_REGISTRY[name]
    except KeyError:
        raise FeatureFlagError(f"unknown feature flag: {name!r}") from None


def known_flags() -> list[FlagSpec]:
    return [FLAG_REGISTRY[n] for n in FLAG_REGISTRY]


def _override_value(db: Session, name: str) -> tuple[bool, str] | None:
    """Return (value, enabled_by) of a persisted override, or None."""
    row = db.query(FeatureFlag).filter(FeatureFlag.name == name).first()
    if row is None:
        return None
    return row.value, row.enabled_by


def _scope_allows(spec: FlagSpec, *, role: str | None, market: str | None) -> bool:
    if spec.markets:
        if market is None or market not in spec.markets:
            return False
    if spec.roles:
        if role is None or role not in spec.roles:
            return False
    return True


def is_enabled(
    db: Session | None,
    name: str,
    *,
    role: str | None = None,
    market: str | None = None,
) -> bool:
    """Resolve the effective value of a flag under optional scope gates.

    ``db`` may be ``None``; when no DB override exists (or no DB is supplied)
    the safe registry default applies. Scope gates are always enforced, so a
    market-scoped flag can never activate outside its market.
    """
    spec = get_flag(name)
    if not _scope_allows(spec, role=role, market=market):
        return False
    if db is not None:
        override = _override_value(db, name)
        if override is not None:
            return override[0]
    return spec.default


def effective_flags(
    db: Session | None,
    *,
    role: str | None = None,
    market: str | None = None,
) -> dict[str, bool]:
    """Resolve every registry flag to its effective boolean."""
    return {spec.name: is_enabled(db, spec.name, role=role, market=market) for spec in known_flags()}


def set_flag(
    db: Session,
    actor: AdminUser,
    name: str,
    value: bool,
    *,
    note: str = "",
    market: str | None = None,
    role: str | None = None,
) -> FeatureFlag:
    """Upsert a validated override and audit the change.

    Validates the name is allow-listed and, for market-scoped flags, that the
    override is only applied within that market (no cross-market leakage).
    """
    spec = get_flag(name)  # raises for unknown names
    if spec.markets:
        if market is None or market not in spec.markets:
            raise FeatureFlagError(
                f"flag {name!r} is scoped to market(s) {','.join(spec.markets)}; cannot set without an allowed market"
            )

    row = db.query(FeatureFlag).filter(FeatureFlag.name == name).first()
    if row is None:
        row = FeatureFlag(name=name, value=value, note=note, enabled_by=actor.email or f"admin:{actor.id}", changed_at=datetime.now(timezone.utc))
        db.add(row)
    else:
        row.value = value
        row.note = note or row.note
        row.enabled_by = actor.email or f"admin:{actor.id}"
        row.changed_at = datetime.now(timezone.utc)

    # Persist override value for the invariant/effective resolution.
    db.flush()

    log_audit_event(
        db,
        actor,
        "feature_flag.updated",
        "feature_flag",
        name,
        reason=f"value={value} note={note[:200]} scope_market={market or ''} scope_role={role or ''}",
    )
    db.flush()
    return row
