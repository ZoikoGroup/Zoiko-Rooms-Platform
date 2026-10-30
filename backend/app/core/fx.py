"""Indicative exchange rates, used only to compare room prices in the Find a Room price filter.

They are NOT used for payments (rent and deposits are paid in the listing's own currency, between
host and renter) and they are not live market rates. The defaults below are rough; set FX_RATES_JSON
(e.g. '{"GBP": 1.31, "INR": 0.0118}') to correct or add currencies, and FX_RATES_AS_OF to say when."""

from __future__ import annotations

import json
import logging

from app.core.config import settings

logger = logging.getLogger("uvicorn.error")

BASE_CURRENCY = "USD"

# How many US dollars one unit of the currency is worth (indicative).
DEFAULT_USD_PER_UNIT: dict[str, float] = {
    "USD": 1.0,
    "GBP": 1.27,
    "EUR": 1.08,
    "INR": 0.012,
    "AED": 0.2723,
    "AUD": 0.65,
    "CAD": 0.73,
}


def usd_per_unit() -> dict[str, float]:
    """The defaults, with any valid overrides from FX_RATES_JSON applied. A bad entry is skipped, not fatal."""
    rates = dict(DEFAULT_USD_PER_UNIT)
    raw = (settings.fx_rates_json or "").strip()
    if not raw:
        return rates
    try:
        overrides = json.loads(raw)
    except ValueError:
        logger.warning("FX_RATES_JSON is not valid JSON -- using the default exchange rates")
        return rates
    if not isinstance(overrides, dict):
        logger.warning("FX_RATES_JSON must be an object like {\"GBP\": 1.31} -- using the default exchange rates")
        return rates
    for code, value in overrides.items():
        try:
            rate = float(value)
        except (TypeError, ValueError):
            continue
        if isinstance(code, str) and len(code) == 3 and rate > 0:
            rates[code.upper()] = rate
    return rates
