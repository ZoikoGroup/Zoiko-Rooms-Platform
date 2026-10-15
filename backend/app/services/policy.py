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
    # suspend / quarantine a live listing at any time. Platform-fixed (see
    # _PLATFORM_FIXED_KEYS): a market can no longer turn admin review back on.
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
    # Party decisions instead of admin review (crud/termination_responses.py,
    # crud/occupancy_lifecycle.py): how long the other side has to answer a
    # termination proposal before silence counts as acceptance; the shortest
    # notice a contested ground may propose; how long a host has to confirm a
    # renter's move-out before it's confirmed for them.
    "termination.response_days": lambda: 14,
    "termination.contested_min_notice_days": lambda: 28,
    "tenancy.move_out_confirm_days": lambda: 7,
    # Rent paid to the host for days after a tenancy ended (crud/
    # tenancy_settlement.py): how long the host has to pay it back, and how
    # long the renter has to confirm before receipt is assumed.
    "refund.host_payment_days": lambda: 14,
    "refund.renter_confirm_days": lambda: 7,
    # A host's reported fix for a habitability issue the renter doesn't
    # answer is treated as confirmed after this many days.
    "habitability.renter_confirm_days": lambda: 5,
    # A high-risk change to a host's payment details (new destination, just
    # after a password reset...) waits this long before taking effect, so a
    # host whose account was taken over can cancel it -- instead of an admin
    # reviewing it.
    "payment.instruction_cooling_off_hours": lambda: 72,
    # A contested host<->renter dispute claim: days to agree a settlement
    # before it goes to the outside body (crud/dispute_party_flow.py).
    "dispute.negotiation_days": lambda: 21,
    # Deposit claims (crud/deposit_party_flow.py): days after a tenancy ends
    # for the host to claim against the deposit -- after that, whatever isn't
    # claimed goes back to the renter on its own -- and days the renter has
    # to answer a claim before it's accepted.
    "deposit.claim_days": lambda: 14,
    "deposit.renter_response_days": lambda: 14,
    # Support tickets (services/support_assistant.py): a ticket waiting on the
    # user closes after this many quiet days; a resolved one closes (no more
    # reopening) after this many.
    "support.action_required_close_days": lambda: 14,
    "support.resolved_close_days": lambda: 7,
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


# Keys a market release can't override: decisions the platform keeps automatic
# everywhere (no manual admin review step). An override already saved on a
# release is ignored, and set_policy_overrides drops new ones.
_PLATFORM_FIXED_KEYS = frozenset({"publication.requires_approval"})


def get_policy(market_release: MarketRelease | None, key: str) -> Any:
    if key not in _DEFAULTS:
        raise KeyError(f"Unknown policy key: {key}")
    if key in _PLATFORM_FIXED_KEYS:
        return _DEFAULTS[key]()
    if market_release is not None and market_release.policy_overrides and key in market_release.policy_overrides:
        return market_release.policy_overrides[key]
    return _DEFAULTS[key]()


def set_policy_overrides(market_release: MarketRelease, overrides: dict[str, Any]) -> None:
    """Replaces the full override set -- callers pass the complete desired
    dict (schema-validated to only ever contain known keys), same shape as
    every other 'set state' crud function in this codebase rather than a
    partial per-key PATCH."""
    overrides = {k: v for k, v in overrides.items() if k not in _RETIRED_KEYS and k not in _PLATFORM_FIXED_KEYS}
    unknown = set(overrides) - set(_DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown policy key(s): {', '.join(sorted(unknown))}")
    market_release.policy_overrides = overrides
