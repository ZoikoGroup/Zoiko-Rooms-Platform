"""ZR-PAY-CFG-001 Sections 4, 5 and 9: the money-movement boundary.

Zoiko Rooms collects its own Listing Fee only. Rent and deposits are paid
directly by the renter to the verified recipient (landlord, host, agent);
Zoiko records instructions, declarations, confirmations and evidence but
never receives, holds, escrows, settles, refunds or pays out rental money,
and never takes a commission on rent.

The legacy code paths that did move rental money through Zoiko (renter
card payments into Zoiko's Stripe account, deposit custody, host payouts,
Stripe Connect payout accounts, rental refunds) stay in the codebase but
every API route into them depends on require_capability(...) below, which
refuses the request unless the matching core/config.py flag is on -- and a
production deployment refuses to boot with any of those flags on.
"""

from __future__ import annotations

from typing import Callable

from fastapi import HTTPException, status

from app.core.config import settings

_REFUSAL_MESSAGES = {
    "rent_collection_enabled": (
        "Zoiko Rooms does not collect rent. Pay your landlord, host or agent directly using the payment "
        "instructions for this rental, then record the payment here."
    ),
    "deposit_collection_enabled": (
        "Zoiko Rooms does not collect or hold deposits. Deposits are paid to and returned by the authorized "
        "recipient or deposit scheme directly."
    ),
    "host_payouts_enabled": (
        "Zoiko Rooms does not pay out rental money. Renters pay you directly using your verified payment "
        "instructions."
    ),
    "escrow_enabled": "Zoiko Rooms does not hold funds in escrow.",
    "wallet_enabled": "Zoiko Rooms does not maintain wallet balances.",
    "split_settlement_enabled": "Zoiko Rooms does not settle or split rental payments.",
}


def capability_enabled(flag: str) -> bool:
    if flag not in _REFUSAL_MESSAGES:
        raise KeyError(f"Unknown payment capability: {flag}")
    return bool(getattr(settings, flag))


def assert_capability(flag: str) -> None:
    if not capability_enabled(flag):
        raise HTTPException(status.HTTP_403_FORBIDDEN, _REFUSAL_MESSAGES[flag])


def require_capability(*flags: str) -> Callable[[], None]:
    """FastAPI dependency: refuses the request unless every named capability
    is enabled. Use in a route's dependencies=[Depends(...)]."""
    for flag in flags:
        if flag not in _REFUSAL_MESSAGES:
            raise KeyError(f"Unknown payment capability: {flag}")

    def _guard() -> None:
        for flag in flags:
            assert_capability(flag)

    return _guard


def capabilities_snapshot() -> dict[str, bool]:
    """What the frontend reads to decide which payment actions to show --
    it never infers payment capability on its own (ZR-PAY-CFG-001 9.1)."""
    return {
        "listing_fee_enabled": True,
        "rental_payment_records_enabled": True,
        "rental_payment_instructions_enabled": True,
        "rental_money_movement_via_zoiko": any(capability_enabled(f) for f in _REFUSAL_MESSAGES),
        **{flag: capability_enabled(flag) for flag in _REFUSAL_MESSAGES},
        "platform_fee_rate": None,
        "host_commission_enabled": False,
    }
