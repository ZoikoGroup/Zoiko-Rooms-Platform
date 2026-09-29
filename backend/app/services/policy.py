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
    # Rule 3: every market requires an admin/super-admin review before a
    # listing goes live. After approval the host pays the Listing Fee and
    # the listing publishes automatically (crud/listing_fee.py:
    # _complete_payment_success). A market release can still override this
    # to False to auto-approve+publish via _auto_approve_and_publish_low_risk_market.
    "publication.requires_approval": lambda: True,
    # Same status: readable/settable, not yet consulted -- no call site in
    # this codebase currently branches public-visibility behavior on a
    # failed jurisdiction gate beyond "not bookable" (Rule 1's own
    # PUBLICATION_ELIGIBLE/JURISDICTION_GATES_PASS clauses already prevent
    # booking either way).
    "visibility.failed_gate_behavior": lambda: "hide",
    # When False, crud/leasing.py:submit_application auto-approves the
    # application the moment it's submitted (system-attributed, same real
    # ApplicationDecision row and notifications a host's own approve click
    # produces -- see decide_application's own docstring on this being a
    # trust & safety decision, now delegated to policy instead of always
    # requiring a human). Approving still only starts the offer/agreement
    # chain below if those are also opted in; it never itself bypasses
    # check_offer_eligibility/check_agreement_eligibility.
    "application.requires_manual_decision": lambda: False,
    # Default False: crud/leasing.py auto-creates and sends an offer (using
    # the listing's own default terms) the moment an application is
    # approved -- never bypasses check_offer_eligibility, just skips the
    # human "click to proceed." A market release can override it to True.
    "offer.requires_manual_creation": lambda: False,
    # Same as above, for the offer-accepted -> agreement step. When False,
    # crud/leasing.py auto-creates the agreement the moment an offer is
    # accepted -- create_agreement still enforces check_agreement_eligibility
    # itself, so this never bypasses any compliance gate; it only removes the
    # manual "Create Agreement" click once every gate already passes.
    "agreement.requires_manual_creation": lambda: False,
    # ZR-PAY-CFG-001 Sections 5.1/9: "external_payment_handoff_enabled =
    # MARKET_APPROVED_ONLY". Renters may be sent to the recipient's own
    # payment provider (a direct charge on the recipient's connected
    # account -- Zoiko is never in the flow of funds) only in a market an
    # admin has explicitly approved it for. Off everywhere by default;
    # renters otherwise pay using the recipient's payment instructions.
    "payment.external_handoff_approved": lambda: False,
}

POLICY_KEYS = tuple(_DEFAULTS)


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
    unknown = set(overrides) - set(_DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown policy key(s): {', '.join(sorted(unknown))}")
    market_release.policy_overrides = dict(overrides)
