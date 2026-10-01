"""Address lookup for property verification: does the address the host gave
actually exist on a map, precisely enough, in the country of the property's
region?

Providers (settings.geocoding_provider):
- "google": Google Geocoding API (needs settings.google_maps_api_key)
- "nominatim": OpenStreetMap Nominatim (free; identifying User-Agent and at
  most one request per second, per its usage policy)
- "auto" (default): Google when a key is configured, otherwise Nominatim
- "none": no lookup -- every result is UNAVAILABLE

geocode_address() never raises: network/provider failures come back as
UNAVAILABLE so the caller can route the submission to manual review instead
of failing the host's upload."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.parse import quote_plus

import httpx

from app.core.config import settings

logger = logging.getLogger("uvicorn.error")

# Outcomes. Only FOUND lets a property verification complete automatically.
FOUND = "FOUND"
NOT_FOUND = "NOT_FOUND"
IMPRECISE = "IMPRECISE"
COUNTRY_MISMATCH = "COUNTRY_MISMATCH"
UNAVAILABLE = "UNAVAILABLE"
GEOCODE_STATUSES = (FOUND, NOT_FOUND, IMPRECISE, COUNTRY_MISMATCH, UNAVAILABLE)

# How precisely the address resolved, best first. HOUSE/STREET count as the
# address existing; LOCALITY/REGION only prove the town or area exists.
PRECISIONS = ("HOUSE", "STREET", "LOCALITY", "REGION")
ACCEPTED_PRECISIONS = ("HOUSE", "STREET")

# Property.jurisdiction_code -> ISO 3166-1 alpha-2 country the address must
# be in. A code missing here skips the country check (unknown region).
JURISDICTION_COUNTRIES: dict[str, str] = {
    "England": "GB",
    "GB-ENG": "GB",
    "GB-WLS": "GB",
    "GB-SCT": "GB",
    "GB-NIR": "GB",
    "IN": "IN",
}
COUNTRY_NAMES: dict[str, str] = {"GB": "United Kingdom", "IN": "India"}


@dataclass
class GeocodeResult:
    status: str
    provider: str
    query: str
    latitude: float | None = None
    longitude: float | None = None
    formatted_address: str = ""
    precision: str = ""
    country_code: str = ""
    expected_country_code: str = ""
    detail: str = ""
    geocoded_at: str = ""

    @property
    def is_found(self) -> bool:
        return self.status == FOUND

    def as_dict(self) -> dict:
        return asdict(self)


def google_maps_url(latitude: float | None, longitude: float | None, fallback_query: str = "") -> str:
    """Public Google Maps link (no API key needed) for showing the location."""
    if latitude is not None and longitude is not None:
        return f"https://www.google.com/maps/search/?api=1&query={latitude:.6f},{longitude:.6f}"
    if fallback_query:
        return f"https://www.google.com/maps/search/?api=1&query={quote_plus(fallback_query)}"
    return ""


def build_address_query(address: str, city: str, landmark: str | None, jurisdiction_code: str) -> str:
    country = COUNTRY_NAMES.get(JURISDICTION_COUNTRIES.get(jurisdiction_code, ""), "")
    parts = [address, landmark or "", city, country]
    seen: set[str] = set()
    cleaned = []
    for part in parts:
        value = (part or "").strip().strip(",")
        if value and value.lower() not in seen:
            seen.add(value.lower())
            cleaned.append(value)
    return ", ".join(cleaned)


def active_provider() -> str:
    provider = (settings.geocoding_provider or "auto").lower()
    if provider == "auto":
        return "google" if settings.google_maps_api_key else "nominatim"
    return provider


def geocode_address(address: str, city: str, landmark: str | None, jurisdiction_code: str) -> GeocodeResult:
    query = build_address_query(address, city, landmark, jurisdiction_code)
    expected = JURISDICTION_COUNTRIES.get(jurisdiction_code, "")
    provider = active_provider()
    now = datetime.now(timezone.utc).isoformat()

    if not query:
        return GeocodeResult(NOT_FOUND, provider, query, expected_country_code=expected,
                             detail="No address was given", geocoded_at=now)
    if provider == "none":
        return GeocodeResult(UNAVAILABLE, provider, query, expected_country_code=expected,
                             detail="Address lookup is disabled", geocoded_at=now)

    try:
        if provider == "google":
            if not settings.google_maps_api_key:
                return GeocodeResult(UNAVAILABLE, provider, query, expected_country_code=expected,
                                     detail="Google Maps API key is not configured", geocoded_at=now)
            hit = _google(query, expected)
        elif provider == "nominatim":
            hit = _nominatim(query)
        else:
            return GeocodeResult(UNAVAILABLE, provider, query, expected_country_code=expected,
                                 detail=f"Unknown geocoding provider '{provider}'", geocoded_at=now)
    except _ProviderUnavailable as exc:
        logger.warning("geocoding unavailable (%s): %s", provider, exc)
        return GeocodeResult(UNAVAILABLE, provider, query, expected_country_code=expected,
                             detail=str(exc), geocoded_at=now)

    if hit is None:
        return GeocodeResult(NOT_FOUND, provider, query, expected_country_code=expected,
                             detail="The address could not be found on the map", geocoded_at=now)

    result = GeocodeResult(
        FOUND, provider, query, latitude=hit["lat"], longitude=hit["lng"],
        formatted_address=hit["formatted"], precision=hit["precision"],
        country_code=hit["country"], expected_country_code=expected, geocoded_at=now,
    )
    if expected and result.country_code and result.country_code != expected:
        result.status = COUNTRY_MISMATCH
        result.detail = (
            f"The address was found in {COUNTRY_NAMES.get(result.country_code, result.country_code)}, "
            f"but the property is listed under {jurisdiction_code}"
        )
    elif result.precision not in ACCEPTED_PRECISIONS:
        result.status = IMPRECISE
        result.detail = f"Only the {result.precision.lower()} was found, not the street or building"
    return result


class _ProviderUnavailable(Exception):
    pass


# --------------------------------------------------------------- providers

def _google(query: str, expected_country: str) -> dict | None:
    params = {"address": query, "key": settings.google_maps_api_key}
    if expected_country:
        params["region"] = expected_country.lower()
    try:
        response = httpx.get(
            "https://maps.googleapis.com/maps/api/geocode/json", params=params,
            timeout=settings.geocoding_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise _ProviderUnavailable(f"Google geocoding request failed: {exc}") from exc

    status = payload.get("status")
    if status == "ZERO_RESULTS":
        return None
    if status != "OK" or not payload.get("results"):
        raise _ProviderUnavailable(f"Google geocoding returned {status}: {payload.get('error_message', '')}".strip())

    top = payload["results"][0]
    location = top["geometry"]["location"]
    country = next(
        (c.get("short_name", "") for c in top.get("address_components", []) if "country" in c.get("types", [])), "",
    )
    return {
        "lat": float(location["lat"]),
        "lng": float(location["lng"]),
        "formatted": top.get("formatted_address", ""),
        "precision": _google_precision(top),
        "country": country.upper(),
    }


def _google_precision(result: dict) -> str:
    location_type = result.get("geometry", {}).get("location_type", "")
    types = set(result.get("types", []))
    if location_type in ("ROOFTOP", "RANGE_INTERPOLATED") or types & {"street_address", "premise", "subpremise"}:
        precision = "HOUSE"
    elif "route" in types:
        precision = "STREET"
    elif types & {"locality", "sublocality", "neighborhood", "postal_code", "postal_town"}:
        precision = "LOCALITY"
    else:
        precision = "REGION"
    # partial_match: Google matched only part of what was asked for.
    if result.get("partial_match") and precision == "HOUSE":
        precision = "STREET"
    return precision


_nominatim_lock = threading.Lock()
_nominatim_last_call = 0.0


def _nominatim(query: str) -> dict | None:
    global _nominatim_last_call
    with _nominatim_lock:
        # Usage policy: absolute maximum of 1 request per second.
        wait = 1.0 - (time.monotonic() - _nominatim_last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            response = httpx.get(
                settings.nominatim_url,
                params={"q": query, "format": "jsonv2", "addressdetails": 1, "limit": 1},
                headers={"User-Agent": settings.nominatim_user_agent},
                timeout=settings.geocoding_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise _ProviderUnavailable(f"OpenStreetMap geocoding request failed: {exc}") from exc
        finally:
            _nominatim_last_call = time.monotonic()

    if not payload:
        return None
    top = payload[0]
    return {
        "lat": float(top["lat"]),
        "lng": float(top["lon"]),
        "formatted": top.get("display_name", ""),
        "precision": _nominatim_precision(int(top.get("place_rank") or 0)),
        "country": (top.get("address", {}).get("country_code") or "").upper(),
    }


def _nominatim_precision(place_rank: int) -> str:
    # Nominatim place_rank: 30 house/building, 26-27 street, 16-25 town/suburb, <16 region/country.
    if place_rank >= 28:
        return "HOUSE"
    if place_rank >= 26:
        return "STREET"
    if place_rank >= 16:
        return "LOCALITY"
    return "REGION"
