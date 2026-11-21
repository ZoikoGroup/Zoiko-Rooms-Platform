"""Detect which currency a visitor most likely thinks in, so the Find a Room price filter can offer
limits in it instead of a fixed currency.

Order of evidence (first one that gives a usable currency wins; every step falls through to the next
if it is missing, down or has no answer, so no single source can leave a visitor without a currency):
  1. A country header set by the CDN / host in front of the app (Cloudflare, Vercel, CloudFront) --
     free, instant, and the visitor's IP never leaves our infrastructure.
  2. The offline DB-IP Lite database (GEOIP_DATABASE_PATH) -- the IP is looked up on this server only.
  3. Online lookups, in turn: IP_GEOLOCATION_URL then IP_GEOLOCATION_FALLBACK_URL (empty = off).
     Private/loopback addresses (local development) are never looked up anywhere.
  4. The browser's time zone (``tz``), then the country in its language setting (``region``).
Country -> currency comes from CLDR (Babel), with a built-in table if Babel isn't installed.

The result is only ever a hint for the price filter: it is never used for payments or listing prices.
Only currencies we hold an indicative exchange rate for are returned, so the filter can always
compare rooms in other currencies with the limit."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import threading
import time
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.fx import usd_per_unit

logger = logging.getLogger("uvicorn.error")

_COUNTRY_HEADERS = ("cf-ipcountry", "x-vercel-ip-country", "cloudfront-viewer-country", "x-country-code")
_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")

_EURO = (
    "AT BE CY DE EE ES FI FR GR HR IE IT LT LU LV MT NL PT SI SK AD MC SM VA ME XK"
).split()

COUNTRY_CURRENCY: dict[str, str] = {
    "US": "USD", "GB": "GBP", "IN": "INR", "AE": "AED", "AU": "AUD", "CA": "CAD", "SG": "SGD", "NZ": "NZD",
    "CH": "CHF", "SE": "SEK", "NO": "NOK", "DK": "DKK", "PL": "PLN", "CZ": "CZK", "HU": "HUF", "RO": "RON",
    "JP": "JPY", "CN": "CNY", "HK": "HKD", "KR": "KRW", "TH": "THB", "MY": "MYR", "ID": "IDR", "PH": "PHP",
    "VN": "VND", "PK": "PKR", "BD": "BDT", "LK": "LKR", "NP": "NPR", "SA": "SAR", "QA": "QAR", "KW": "KWD",
    "BH": "BHD", "OM": "OMR", "IL": "ILS", "TR": "TRY", "EG": "EGP", "ZA": "ZAR", "NG": "NGN", "KE": "KES",
    "BR": "BRL", "MX": "MXN", "AR": "ARS", "CL": "CLP", "CO": "COP", "PE": "PEN",
    **{c: "EUR" for c in _EURO},
}

_TZ_COUNTRY: dict[str, str] = {
    "Asia/Kolkata": "IN", "Asia/Calcutta": "IN", "Asia/Dubai": "AE", "Asia/Singapore": "SG",
    "Europe/London": "GB", "Europe/Belfast": "GB", "Europe/Dublin": "IE", "Europe/Paris": "FR",
    "Europe/Berlin": "DE", "Europe/Madrid": "ES", "Europe/Rome": "IT", "Europe/Amsterdam": "NL",
    "Europe/Brussels": "BE", "Europe/Lisbon": "PT", "Europe/Vienna": "AT", "Europe/Athens": "GR",
    "Europe/Helsinki": "FI", "Europe/Luxembourg": "LU", "Europe/Tallinn": "EE", "Europe/Riga": "LV",
    "Europe/Vilnius": "LT", "Europe/Zagreb": "HR", "Europe/Malta": "MT", "Europe/Bratislava": "SK",
    "Europe/Ljubljana": "SI", "Pacific/Auckland": "NZ", "Pacific/Honolulu": "US",
}
_CANADA_ZONES = {"Toronto", "Vancouver", "Edmonton", "Winnipeg", "Halifax", "Regina", "St_Johns", "Montreal",
                 "Calgary", "Ottawa", "Moncton", "Whitehorse", "Yellowknife", "Iqaluit", "Glace_Bay"}
_US_ZONES = {"New_York", "Chicago", "Denver", "Los_Angeles", "Phoenix", "Anchorage", "Detroit", "Boise",
             "Juneau", "Adak", "Nome", "Sitka", "Menominee"}


def country_from_timezone(tz: str | None) -> str | None:
    """The country a browser time zone (``Intl...timeZone``, e.g. "Asia/Kolkata") belongs to, for the
    zones we can be sure about. More reliable than the browser's language, which says nothing about where
    someone is (``en-US`` is common everywhere)."""
    tz = (tz or "").strip()
    if not tz or len(tz) > 64 or not re.match(r"^[A-Za-z_]+(/[A-Za-z_+\-0-9]+){1,2}$", tz):
        return None
    if tz in _TZ_COUNTRY:
        return _TZ_COUNTRY[tz]
    if tz.startswith("Australia/"):
        return "AU"
    if tz.startswith("US/"):
        return "US"
    parts = tz.split("/")
    if parts[0] == "America":
        if parts[1] in _CANADA_ZONES:
            return "CA"
        if parts[1] in _US_ZONES or parts[1] in ("Indiana", "Kentucky", "North_Dakota"):
            return "US"
    return None


_CACHE_TTL_SECONDS = 6 * 3600
_CACHE_MAX = 5000
_country_cache: dict[str, tuple[float, str | None]] = {}


def _currency_of(country: str) -> str | None:
    """The currency a country uses today: CLDR via Babel (covers every country), else the built-in table."""
    try:
        from babel.numbers import get_territory_currencies

        found = get_territory_currencies(country)
        if found:
            return found[0]
    except Exception:  # Babel missing or no data for this code -- use the table
        pass
    return COUNTRY_CURRENCY.get(country)


def currency_for_country(country: str | None) -> str | None:
    """The currency used in an ISO-3166 country, but only when we can compare it (hold a rate for it)."""
    if not country or not _COUNTRY_RE.match(country):
        return None
    currency = _currency_of(country.upper())
    return currency if currency and currency in usd_per_unit() else None


def client_ip(headers, peer: str | None) -> str:
    """First X-Forwarded-For hop when behind a proxy, otherwise the direct peer."""
    forwarded = headers.get("x-forwarded-for")
    return (forwarded.split(",")[0].strip() if forwarded else "") or (peer or "")


def _is_public_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def country_from_headers(headers) -> str | None:
    for name in _COUNTRY_HEADERS:
        value = (headers.get(name) or "").strip()
        # "XX" / "T1" are Cloudflare's "unknown" / "Tor" markers, not countries.
        if _COUNTRY_RE.match(value) and value.upper() not in ("XX",):
            return value.upper()
    return None


_db_lock = threading.Lock()
_db_reader = None
_db_signature: tuple | None = None  # (path, mtime) of the file the reader was opened from


def _geoip_path() -> Path | None:
    raw = (settings.geoip_database_path or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else Path(__file__).resolve().parents[2] / path  # relative to backend/


def _geoip_reader():
    """The offline DB-IP database. Opened on first use and re-opened when update_geoip.py replaces the file,
    so the monthly refresh needs no restart. None when the file or maxminddb isn't there."""
    global _db_reader, _db_signature
    path = _geoip_path()
    try:
        signature = (str(path), path.stat().st_mtime) if path is not None and path.is_file() else None
    except OSError:
        signature = None
    if signature == _db_signature and (signature is not None or _db_signature is None):
        return _db_reader
    with _db_lock:
        if signature == _db_signature:
            return _db_reader
        reader = None
        if signature is not None:
            try:
                import maxminddb

                reader = maxminddb.open_database(signature[0])
            except Exception as exc:
                logger.warning("visitor currency: GeoIP database unusable (%s) -- using online lookups", exc.__class__.__name__)
        elif path is not None:
            logger.info("visitor currency: no GeoIP database at %s -- using online lookups", path)
        _db_reader, _db_signature = reader, signature
    return _db_reader


def country_from_database(ip: str) -> str | None:
    """Offline lookup. Never raises, never leaves this server."""
    if not _is_public_ip(ip):
        return None
    reader = _geoip_reader()
    if reader is None:
        return None
    try:
        record = reader.get(ip) or {}
        code = ((record.get("country") or {}).get("iso_code") or "").strip()
        return code.upper() if _COUNTRY_RE.match(code) else None
    except Exception:
        return None


def _parse_country(text: str) -> str | None:
    text = text.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            return None
        for key in ("country", "country_code", "countryCode"):
            value = data.get(key)
            if isinstance(value, str) and _COUNTRY_RE.match(value.strip()):
                return value.strip().upper()
        return None
    text = text.strip('"')
    return text.upper() if _COUNTRY_RE.match(text) else None


def country_from_online(ip: str) -> str | None:
    """Ask the configured online services in turn until one answers. Cached; never raises."""
    templates = [t.strip() for t in (settings.ip_geolocation_url, settings.ip_geolocation_fallback_url) if (t or "").strip()]
    if not templates or not _is_public_ip(ip):
        return None
    now = time.monotonic()
    cached = _country_cache.get(ip)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    country: str | None = None
    for template in templates:
        try:
            response = httpx.get(template.replace("{ip}", ip), timeout=2.5, headers={"Accept": "application/json, text/plain"})
            if response.status_code == 200:
                country = _parse_country(response.text)
                if country:
                    break
        except httpx.HTTPError as exc:
            logger.warning("visitor currency: IP lookup failed (%s)", exc.__class__.__name__)
    if len(_country_cache) >= _CACHE_MAX:
        _country_cache.clear()
    # Cache a total failure only briefly's worth by simply not storing it, so the next visit tries again.
    if country:
        _country_cache[ip] = (now, country)
    return country


def country_from_ip(ip: str) -> str | None:
    """Offline database first, then the online services."""
    return country_from_database(ip) or country_from_online(ip)


def detect_visitor_currency(headers, peer: str | None, region_hint: str | None = None, timezone_hint: str | None = None) -> dict:
    """{"currency": "GBP" | None, "country": "GB" | None, "source": "header" | "ip" | "browser" | None}"""
    country = country_from_headers(headers)
    source = "header" if country else None
    if not country or not currency_for_country(country):
        looked_up = country_from_ip(client_ip(headers, peer))
        if looked_up and currency_for_country(looked_up):
            country, source = looked_up, "ip"
    if not country or not currency_for_country(country):
        # Browser hints: the time zone says where the device is; the language region is the weakest guess.
        for candidate in (country_from_timezone(timezone_hint), (region_hint or "").strip().upper()):
            if candidate and _COUNTRY_RE.match(candidate) and currency_for_country(candidate):
                country, source = candidate, "browser"
                break
    currency = currency_for_country(country)
    return {"currency": currency, "country": country if currency else None, "source": source if currency else None}
