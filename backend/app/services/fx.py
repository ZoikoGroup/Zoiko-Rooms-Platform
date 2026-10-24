"""Converting amounts into US dollars for reporting (the super admin
dashboard's revenue). Uses the fixed rates in settings.fx_rates_to_usd --
nothing is fetched from outside -- and is for reporting only: money is
always charged and recorded in its own currency."""

from __future__ import annotations

from app.core.config import settings

REPORTING_CURRENCY = "USD"


def usd_rate(currency: str) -> float | None:
    """US dollars per one unit of `currency`, or None when no rate is set."""
    code = (currency or "").strip().upper()
    for key, rate in settings.fx_rates_to_usd.items():
        if key.strip().upper() == code:
            return float(rate)
    return None


def to_usd(amount: float, currency: str) -> float | None:
    """`amount` in US dollars, or None when `currency` has no rate."""
    rate = usd_rate(currency)
    return None if rate is None else amount * rate
