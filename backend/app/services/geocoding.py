"""Address lookup for the legacy room-level property verification: does the
address the host gave resolve on a map, precisely enough, in the country of
the property's region?

Goes through the ZR-PROPERTY-VERIFY-001 location adapter
(services/location.py) -- Google Maps Platform primary, Mapbox / HERE
fallbacks -- so there is one provider integration. With no provider
configured every result is UNAVAILABLE.

geocode_address() never raises: network/provider failures come back as
UNAVAILABLE so the caller can route the submission to manual review instead
of failing the host's upload."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.parse import quote_plus


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
    from app.services import location as loc

    return loc.primary_provider().code


_PRECISION = {"ROOFTOP": "HOUSE", "PARCEL": "HOUSE", "INTERPOLATED": "HOUSE", "STREET": "STREET",
              "APPROXIMATE": "LOCALITY"}


def geocode_address(address: str, city: str, landmark: str | None, jurisdiction_code: str) -> GeocodeResult:
    from app.services import location as loc

    query = build_address_query(address, city, landmark, jurisdiction_code)
    expected = JURISDICTION_COUNTRIES.get(jurisdiction_code, "")
    provider = active_provider()
    now = datetime.now(timezone.utc).isoformat()

    if not query:
        return GeocodeResult(NOT_FOUND, provider, query, expected_country_code=expected,
                             detail="No address was given", geocoded_at=now)
    if provider == "none":
        return GeocodeResult(UNAVAILABLE, provider, query, expected_country_code=expected,
                             detail="No location provider is configured", geocoded_at=now)
    try:
        result = loc.validate(loc.CanonicalAddress(address_line_1=", ".join(p for p in (address, landmark or "") if p),
                                                   locality=city, country_code=expected))
    except loc.LocationUnavailable as exc:
        logger.warning("geocoding unavailable (%s): %s", provider, exc)
        return GeocodeResult(UNAVAILABLE, provider, query, expected_country_code=expected,
                             detail="Map lookup was unavailable", geocoded_at=now)
    provider = result.provider or provider
    if result.location is None:
        return GeocodeResult(NOT_FOUND, provider, query, expected_country_code=expected,
                             detail="The address could not be found on the map", geocoded_at=now)

    out = GeocodeResult(
        FOUND, provider, query, latitude=result.location.latitude, longitude=result.location.longitude,
        formatted_address=result.canonical.formatted, precision=_PRECISION.get(result.location.precision, "LOCALITY"),
        country_code=result.canonical.country_code, expected_country_code=expected, geocoded_at=now,
    )
    if expected and out.country_code and out.country_code != expected:
        out.status = COUNTRY_MISMATCH
        out.detail = (
            f"The address was found in {COUNTRY_NAMES.get(out.country_code, out.country_code)}, "
            f"but the property is listed under {jurisdiction_code}"
        )
    elif out.precision not in ACCEPTED_PRECISIONS:
        out.status = IMPRECISE
        out.detail = f"Only the {out.precision.lower()} was found, not the street or building"
    return out
