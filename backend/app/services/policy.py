"""ZR-ENG-CLR-001 Section 14: Jurisdiction Adaptation Framework -- a thin
policy adapter, not a full ruleset engine ("Global Core with jurisdiction
adaptation hooks" -- the spec explicitly allows starting with only the hooks
this codebase actually branches on and expanding later).

Each MarketRelease may override a small, typed set of policy keys in its
`policy_overrides` JSON column; get_policy() reads the override when present
and falls back to the platform-wide default (from app.core.config.settings,
or a hard default for a key settings doesn't have) otherwise. England launch
never needs an override -- this exists so a later market pack can change one
of these numbers, or (for publication_requires_approval) a future low-risk
approval mode, without forking any state-machine code (14, "England launch
principle").
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.models.market_release import MarketRelease

# Policy key -> platform-wide default. Kept in one place so a new key's
# default is declared exactly once, next to every other key, rather than
# scattered across whichever module happens to consume it.
_DEFAULTS: dict[str, Any] = {
    "booking.acceptance_hold_duration_hours": lambda: settings.offer_acceptance_confirmation_hours,
    "payment.checkout_lock_duration_minutes": lambda: settings.payment_checkout_lock_minutes,
    # Publication is automatic: a listing whose host has completed identity,
    # property and authority verification (and meets the listing rules) is
    # approved by the system at submit and goes live -- straight away, or as
    # soon as the Listing Fee is paid. Super admins are notified and can
    # suspend / quarantine a live listing at any time. A market release may
    # override this to True to send every listing to admin review instead.
    "publication.requires_approval": lambda: False,
    # Same status: readable/settable, not yet consulted -- no call site in
    # this codebase currently branches public-visibility behavior on a
    # failed jurisdiction gate beyond "not bookable" (Rule 1's own
    # PUBLICATION_ELIGIBLE/JURISDICTION_GATES_PASS clauses already prevent
    # booking either way).
    "visibility.failed_gate_behavior": lambda: "hide",
    # ZR-PAY-CFG-001 Sections 5.1/9: "external_payment_handoff_enabled =
    # MARKET_APPROVED_ONLY". Renters may be sent to the recipient's own
    # payment provider (a direct charge on the recipient's connected
    # account -- Zoiko is never in the flow of funds) only in a market an
    # admin has explicitly approved it for. Off everywhere by default;
    # renters otherwise pay using the recipient's payment instructions.
    "payment.external_handoff_approved": lambda: False,
}

POLICY_KEYS = tuple(_DEFAULTS)

# Keys that used to exist (automatic application approval / offer /
# agreement creation -- that flow is now always manual between host and
# renter). A market release saved before their removal may still carry
# them; set_policy_overrides drops them rather than rejecting the save.
_RETIRED_KEYS = frozenset({
    "application.requires_manual_decision",
    "offer.requires_manual_creation",
    "agreement.requires_manual_creation",
})


def get_policy(market_release: MarketRelease | None, key: str) -> Any:
    if key not in _DEFAULTS:
        raise KeyError(f"Unknown policy key: {key}")
    if market_release is not None and market_release.policy_overrides and key in market_release.policy_overrides:
        return market_release.policy_overrides[key]
    return _DEFAULTS[key]()


def set_policy_overrides(market_release: MarketRelease, overrides: dict[str, Any]) -> None:
    """Replaces the full override set -- callers pass the complete desired
    dict (schema-validated to only ever contain known keys), same shape as
    every other 'set state' crud function in this codebase rather than a
    partial per-key PATCH."""
    overrides = {k: v for k, v in overrides.items() if k not in _RETIRED_KEYS}
    unknown = set(overrides) - set(_DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown policy key(s): {', '.join(sorted(unknown))}")
    market_release.policy_overrides = overrides
