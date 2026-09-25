"""ZR-PAY-002 Section 10: 'The global UI must not assume ... a universal
legal meaning for "deposit".' Same shape as app/services/bank_identifiers.py
-- a plain per-jurisdiction lookup, not a MarketPolicyPack column, because
this is display terminology (what a renter is shown), not a legal/financial
rule a market pack governs. Resolution fails OPEN to a generic, honest
default for an unconfigured jurisdiction -- unlike bank_identifiers.py's
fail-closed posture, nothing here gates a financial action, so there is no
harm in falling back to a plain label rather than raising."""

from __future__ import annotations

# Real, commonly-used terms for the same underlying concept (ZR-PAY-002
# Section 6: DEPOSIT-type RentalPaymentObligation/RentalPaymentRecord) --
# not an exhaustive list of every jurisdiction, just the ones this codebase
# already names elsewhere (models/property.py's own jurisdiction_code
# default, crud/market_policy.py:DEFAULT_JURISDICTION). Widening this dict
# later is additive and safe.
DEPOSIT_TERMINOLOGY: dict[str, str] = {
    "England": "tenancy deposit",
    "IN": "security deposit",
    "US": "security deposit",
    "AU": "bond",
}

DEFAULT_DEPOSIT_TERM = "security deposit"


def resolve_deposit_terminology(jurisdiction_code: str | None) -> str:
    if not jurisdiction_code:
        return DEFAULT_DEPOSIT_TERM
    return DEPOSIT_TERMINOLOGY.get(jurisdiction_code, DEFAULT_DEPOSIT_TERM)
