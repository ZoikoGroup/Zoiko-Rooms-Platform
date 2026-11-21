"""Indicative exchange rates, used only to compare room prices in the Find a Room price filter.

They are NOT used for payments (rent and deposits are paid in the listing's own currency, between
host and renter) and they are not live market rates. The defaults below are rough; set FX_RATES_JSON
(e.g. '{"GBP": 1.31, "INR": 0.0118}') to correct or add currencies, and FX_RATES_AS_OF to say when."""

from __future__ import annotations

import json
import logging
import threading
import time

import httpx

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


# --- Live indicative rates (free sources, tried in order) ----------------------------------------------------
# Frankfurter (ECB and other central-bank reference rates, MIT-licensed, self-hostable) first, then
# open.er-api.com. If both are down we keep the last rates we got, and failing that the built-in table above,
# so the price filter never loses its rates. Refreshed in a background thread: a request never waits on them.

_live_rates: dict[str, float] = {}
_live_as_of: str | None = None
_live_fetched_at = 0.0
_refreshing = False
_live_lock = threading.Lock()
_RETRY_AFTER_FAILURE_SECONDS = 15 * 60


def _clean(rates: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for code, value in rates.items():
        try:
            rate = float(value)
        except (TypeError, ValueError):
            continue
        if isinstance(code, str) and len(code) == 3 and code.isalpha() and rate > 0:
            out[code.upper()] = rate
    return out


def _fetch_frankfurter() -> tuple[dict[str, float], str | None]:
    data = httpx.get("https://api.frankfurter.dev/v1/latest", params={"base": "USD"}, timeout=6.0).json()
    per_usd = _clean(data["rates"])
    per_usd["USD"] = 1.0
    return {code: 1.0 / rate for code, rate in per_usd.items()}, data.get("date")


def _fetch_open_er_api() -> tuple[dict[str, float], str | None]:
    data = httpx.get("https://open.er-api.com/v6/latest/USD", timeout=6.0).json()
    if data.get("result") != "success":
        raise ValueError("open.er-api.com did not return rates")
    per_usd = _clean(data["rates"])
    per_usd["USD"] = 1.0
    updated = str(data.get("time_last_update_utc") or "")
    return {code: 1.0 / rate for code, rate in per_usd.items()}, (updated[5:16].strip() or None)


LIVE_SOURCES = (("frankfurter", _fetch_frankfurter), ("open.er-api", _fetch_open_er_api))


def refresh_live_rates() -> bool:
    """Fetch fresh rates from the first free source that answers. Never raises; False when every source failed."""
    global _live_rates, _live_as_of, _live_fetched_at
    for name, fetch in LIVE_SOURCES:
        try:
            rates, as_of = fetch()
        except Exception as exc:  # network, bad JSON, missing keys -- try the next source
            logger.warning("fx: %s unavailable (%s)", name, exc.__class__.__name__)
            continue
        if rates.get("GBP") and rates.get("INR"):  # sanity: a response without the majors isn't usable
            with _live_lock:
                _live_rates, _live_as_of, _live_fetched_at = rates, as_of, time.monotonic()
            logger.info("fx: refreshed indicative rates from %s (%d currencies)", name, len(rates))
            return True
    with _live_lock:
        # Keep whatever we had; look again soon rather than waiting out the full refresh interval.
        _live_fetched_at = time.monotonic() - settings.fx_live_refresh_hours * 3600 + _RETRY_AFTER_FAILURE_SECONDS
    return False


def _refresh_in_background() -> None:
    global _refreshing
    try:
        refresh_live_rates()
    finally:
        _refreshing = False


def _maybe_refresh() -> None:
    global _refreshing
    if not settings.fx_live_enabled:
        return
    with _live_lock:
        stale = (time.monotonic() - _live_fetched_at) > settings.fx_live_refresh_hours * 3600 or not _live_rates
        if not stale or _refreshing:
            return
        _refreshing = True
    threading.Thread(target=_refresh_in_background, name="fx-refresh", daemon=True).start()


def live_rates_as_of() -> str | None:
    """The date of the live rates currently in use, or None when only the built-in table is."""
    return _live_as_of if _live_rates and settings.fx_live_enabled else None


def usd_per_unit() -> dict[str, float]:
    """Built-in defaults, then live rates (when fetched), then any valid overrides from FX_RATES_JSON on top.
    A bad entry is skipped, not fatal."""
    _maybe_refresh()
    rates = dict(DEFAULT_USD_PER_UNIT)
    if settings.fx_live_enabled and _live_rates:
        rates.update(_live_rates)
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
