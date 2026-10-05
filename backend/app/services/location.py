"""ZR-PROPERTY-VERIFY-001 Sections 3 & 12.2 -- the Zoiko location provider
adapter. Business code only ever sees CanonicalAddress / CanonicalLocation /
ValidationResult / Suggestion; no provider response schema leaks out.

Providers (Section 3):
- "google" (primary): Places API (New) Autocomplete + Place Details,
  Address Validation API, Geocoding API -- server key
  settings.google_maps_api_key, never sent to a browser.
- "mapbox" (secondary): Search Box + Geocoding v6 (settings.mapbox_access_token).
- "here" (secondary): Autocomplete / Lookup / Geocode / Reverse v7
  (settings.here_api_key).
- none configured: manual structured entry + evidence / review path.

Routing: settings.location_provider is primary; settings.
location_fallback_providers (default "mapbox,here") are tried in order on
outage, quota error or a coverage gap. A provider failure never approves
anything -- callers route to manual entry / review (Section 14).
Secrets and full provider responses are never logged."""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field

import httpx

from app.core.config import settings

logger = logging.getLogger("uvicorn.error")

# Normalized precision, best first (Section 2.1 location_precision).
PRECISION_ORDER = ("ROOFTOP", "PARCEL", "INTERPOLATED", "STREET", "APPROXIMATE")
# Precisions good enough to auto-accept the map confirmation.
AUTO_ACCEPT_PRECISIONS = ("ROOFTOP", "PARCEL", "INTERPOLATED")


class LocationUnavailable(Exception):
    """Provider unreachable / refused / unsupported -- never an approval."""


@dataclass
class CanonicalAddress:
    address_line_1: str = ""
    address_line_2: str = ""
    subpremise: str = ""
    locality: str = ""
    administrative_area: str = ""
    postal_code: str = ""
    country_code: str = ""  # ISO 3166-1 alpha-2
    formatted: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "CanonicalAddress":
        data = data or {}
        return cls(**{k: str(data.get(k) or "").strip() for k in cls.__dataclass_fields__})

    def one_line(self) -> str:
        parts = [self.address_line_1, self.address_line_2, self.locality, self.administrative_area, self.postal_code]
        country = COUNTRY_NAMES.get(self.country_code, self.country_code)
        return ", ".join(p for p in [*parts, country] if p)


@dataclass
class CanonicalLocation:
    latitude: float
    longitude: float
    precision: str  # one of PRECISION_ORDER
    geocode_status: str = "RESOLVED"  # RESOLVED | AMBIGUOUS
    place_id: str = ""


@dataclass
class Suggestion:
    id: str
    text: str
    secondary: str = ""


@dataclass
class ValidationResult:
    status: str  # VALIDATED | PARTIAL | UNRESOLVED
    canonical: CanonicalAddress
    suggestion: CanonicalAddress | None = None  # a correction the host may accept
    location: CanonicalLocation | None = None
    missing_fields: list[str] = field(default_factory=list)
    provider: str = ""


COUNTRY_NAMES = {"GB": "United Kingdom", "IN": "India", "US": "United States", "IE": "Ireland", "AU": "Australia",
                 "CA": "Canada", "AE": "United Arab Emirates", "SG": "Singapore", "DE": "Germany", "FR": "France"}


def distance_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance (haversine)."""
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class LocationProvider:
    code = ""
    supports_suggest = False

    def suggest(self, query: str, country: str, session_token: str) -> list[Suggestion]:
        return []

    def retrieve(self, provider_result_id: str, session_token: str) -> tuple[CanonicalAddress, CanonicalLocation | None]:
        raise LocationUnavailable("retrieve not supported")

    def validate(self, address: CanonicalAddress) -> ValidationResult:
        raise NotImplementedError

    def geocode(self, address: CanonicalAddress) -> CanonicalLocation | None:
        raise NotImplementedError

    def reverse(self, latitude: float, longitude: float) -> CanonicalAddress | None:
        raise NotImplementedError

# -- Google ---------------------------------------------------------------------

class GoogleLocationProvider(LocationProvider):
    code = "google"
    supports_suggest = True

    def __init__(self, api_key: str, timeout: float):
        self._key = api_key
        self._timeout = timeout

    def _call(self, method: str, url: str, **kwargs) -> dict:
        try:
            response = httpx.request(method, url, timeout=self._timeout, **kwargs)
        except httpx.HTTPError as exc:
            raise LocationUnavailable(f"google request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            logger.warning("location: google %s returned HTTP %s", url.split("?")[0].rsplit("/", 1)[-1], response.status_code)
            raise LocationUnavailable(f"google returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise LocationUnavailable("google returned invalid JSON") from exc

    def suggest(self, query: str, country: str, session_token: str) -> list[Suggestion]:
        body: dict = {"input": query[:200], "sessionToken": session_token}
        if country and country != "*":
            body["includedRegionCodes"] = [country.lower()]
        data = self._call("POST", "https://places.googleapis.com/v1/places:autocomplete", json=body,
                          headers={"X-Goog-Api-Key": self._key})
        out = []
        for item in data.get("suggestions", [])[:8]:
            pred = item.get("placePrediction") or {}
            fmt = pred.get("structuredFormat") or {}
            if pred.get("placeId"):
                out.append(Suggestion(
                    id=pred["placeId"],
                    text=(fmt.get("mainText") or {}).get("text") or (pred.get("text") or {}).get("text", ""),
                    secondary=(fmt.get("secondaryText") or {}).get("text", ""),
                ))
        return out

    def retrieve(self, provider_result_id: str, session_token: str) -> tuple[CanonicalAddress, CanonicalLocation | None]:
        data = self._call(
            "GET", f"https://places.googleapis.com/v1/places/{provider_result_id}",
            params={"sessionToken": session_token},
            headers={"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": "addressComponents,formattedAddress,location,types"},
        )
        address = _google_components([
            {"long_name": c.get("longText", ""), "short_name": c.get("shortText", ""), "types": c.get("types", [])}
            for c in data.get("addressComponents", [])
        ], data.get("formattedAddress", ""))
        loc = data.get("location") or {}
        location = None
        if "latitude" in loc:
            types = set(data.get("types", []))
            precision = "ROOFTOP" if types & {"street_address", "premise", "subpremise"} else (
                "STREET" if "route" in types else "APPROXIMATE")
            location = CanonicalLocation(float(loc["latitude"]), float(loc["longitude"]), precision,
                                         place_id=provider_result_id)
        return address, location

    def validate(self, address: CanonicalAddress) -> ValidationResult:
        lines = [line for line in (address.address_line_1, address.address_line_2) if line]
        if address.subpremise and address.subpremise not in " ".join(lines):
            lines.insert(0, address.subpremise)
        body = {"address": {
            "regionCode": address.country_code, "addressLines": lines, "locality": address.locality,
            "administrativeArea": address.administrative_area, "postalCode": address.postal_code,
        }}
        try:
            data = self._call("POST", "https://addressvalidation.googleapis.com/v1:validateAddress",
                              params={"key": self._key}, json=body)
        except LocationUnavailable:
            # Address Validation doesn't cover every country (it answers 400
            # for an unsupported region). Fall back to the Geocoding API, which
            # is global: the address is located, but only partly validated.
            return self._validate_by_geocode(address)
        result = data.get("result") or {}
        verdict = result.get("verdict") or {}
        postal = (result.get("address") or {}).get("postalAddress") or {}
        formatted = (result.get("address") or {}).get("formattedAddress", "")
        std_lines = postal.get("addressLines") or []
        canonical = CanonicalAddress(
            address_line_1=std_lines[0] if std_lines else address.address_line_1,
            address_line_2=", ".join(std_lines[1:]) if len(std_lines) > 1 else address.address_line_2,
            subpremise=address.subpremise,
            locality=postal.get("locality") or address.locality,
            administrative_area=postal.get("administrativeArea") or address.administrative_area,
            postal_code=postal.get("postalCode") or address.postal_code,
            country_code=(postal.get("regionCode") or address.country_code).upper(),
            formatted=formatted,
        )
        granularity = verdict.get("validationGranularity", "")
        if verdict.get("addressComplete") and not verdict.get("hasUnconfirmedComponents") and granularity in (
                "PREMISE", "SUB_PREMISE", "PREMISE_PROXIMITY"):
            status = "VALIDATED"
        elif granularity in ("PREMISE", "SUB_PREMISE", "PREMISE_PROXIMITY", "BLOCK", "ROUTE"):
            status = "PARTIAL"
        else:
            status = "UNRESOLVED"
        changed = verdict.get("hasReplacedComponents") or verdict.get("hasInferredComponents") or verdict.get("hasSpellCorrectedComponents")
        suggestion = canonical if changed and _differs(address, canonical) else None
        geo = result.get("geocode") or {}
        location = None
        if (geo.get("location") or {}).get("latitude") is not None:
            location = CanonicalLocation(
                float(geo["location"]["latitude"]), float(geo["location"]["longitude"]),
                _google_granularity_precision(verdict.get("geocodeGranularity", "")), place_id=geo.get("placeId", ""),
            )
        return ValidationResult(status, canonical if suggestion is None else address, suggestion, location,
                                provider=self.code)

    def _validate_by_geocode(self, address: CanonicalAddress) -> ValidationResult:
        location = self.geocode(address)  # raises LocationUnavailable if Google itself is down
        if location is None:
            return ValidationResult("UNRESOLVED", address, provider=self.code)
        canonical = CanonicalAddress(**{**address.as_dict(), "formatted": address.one_line()})
        return ValidationResult("PARTIAL", canonical, None, location, provider=self.code)

    def geocode(self, address: CanonicalAddress) -> CanonicalLocation | None:
        params = {"address": address.one_line(), "key": self._key}
        if address.country_code:
            params["components"] = f"country:{address.country_code}"
        data = self._call("GET", "https://maps.googleapis.com/maps/api/geocode/json", params=params)
        status = data.get("status")
        if status == "ZERO_RESULTS":
            return None
        if status != "OK" or not data.get("results"):
            raise LocationUnavailable(f"google geocoding status {status}")
        results = data["results"]
        top = results[0]
        loc = top["geometry"]["location"]
        ambiguous = len(results) > 1 and bool(top.get("partial_match"))
        return CanonicalLocation(float(loc["lat"]), float(loc["lng"]), _google_location_type(top),
                                 "AMBIGUOUS" if ambiguous else "RESOLVED", place_id=top.get("place_id", ""))

    def reverse(self, latitude: float, longitude: float) -> CanonicalAddress | None:
        data = self._call("GET", "https://maps.googleapis.com/maps/api/geocode/json",
                          params={"latlng": f"{latitude},{longitude}", "key": self._key})
        if data.get("status") == "ZERO_RESULTS" or not data.get("results"):
            return None
        top = data["results"][0]
        return _google_components(top.get("address_components", []), top.get("formatted_address", ""))


def _google_components(components: list[dict], formatted: str) -> CanonicalAddress:
    def get(*types, short=False):
        for c in components:
            if set(types) & set(c.get("types", [])):
                return c.get("short_name" if short else "long_name", "")
        return ""

    number, route = get("street_number"), get("route")
    line1 = " ".join(p for p in (number, route) if p) or get("premise")
    return CanonicalAddress(
        address_line_1=line1,
        address_line_2=get("sublocality", "sublocality_level_1", "neighborhood"),
        subpremise=get("subpremise"),
        locality=get("locality", "postal_town") or get("administrative_area_level_2"),
        administrative_area=get("administrative_area_level_1"),
        postal_code=get("postal_code"),
        country_code=get("country", short=True).upper(),
        formatted=formatted,
    )


def _google_location_type(result: dict) -> str:
    location_type = result.get("geometry", {}).get("location_type", "")
    types = set(result.get("types", []))
    if location_type == "ROOFTOP":
        return "ROOFTOP"
    if location_type == "RANGE_INTERPOLATED":
        return "INTERPOLATED"
    if location_type == "GEOMETRIC_CENTER":
        return "PARCEL" if types & {"premise", "subpremise", "establishment"} else "STREET"
    return "APPROXIMATE"


def _google_granularity_precision(granularity: str) -> str:
    return {"SUB_PREMISE": "ROOFTOP", "PREMISE": "ROOFTOP", "PREMISE_PROXIMITY": "INTERPOLATED",
            "BLOCK": "STREET", "ROUTE": "STREET"}.get(granularity, "APPROXIMATE")


def _differs(a: CanonicalAddress, b: CanonicalAddress) -> bool:
    norm = lambda v: " ".join((v or "").lower().replace(",", " ").split())  # noqa: E731
    return any(norm(getattr(a, f)) != norm(getattr(b, f)) for f in
               ("address_line_1", "locality", "administrative_area", "postal_code", "country_code"))


# -- Mapbox (secondary) ----------------------------------------------------------
# Section 3.2: Search Box (suggest / retrieve) + Geocoding v6 (forward /
# reverse) with coordinate accuracy and match-code confidence.

_MAPBOX_ACCURACY = {"rooftop": "ROOFTOP", "parcel": "PARCEL", "point": "ROOFTOP", "interpolated": "INTERPOLATED",
                    "intersection": "STREET", "street": "STREET", "approximate": "APPROXIMATE"}
_AREA_TYPES = {"postcode", "place", "locality", "district", "region", "neighborhood", "country"}


class MapboxLocationProvider(LocationProvider):
    code = "mapbox"
    supports_suggest = True

    def __init__(self, token: str, timeout: float):
        self._token = token
        self._timeout = timeout

    def _get(self, url: str, params: dict) -> dict:
        try:
            response = httpx.get(url, params={**params, "access_token": self._token}, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise LocationUnavailable(f"mapbox request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            logger.warning("location: mapbox %s returned HTTP %s", url.rsplit("/", 2)[-2], response.status_code)
            raise LocationUnavailable(f"mapbox returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise LocationUnavailable("mapbox returned invalid JSON") from exc

    def suggest(self, query: str, country: str, session_token: str) -> list[Suggestion]:
        params = {"q": query[:200], "session_token": session_token or "zoiko", "types": "address", "limit": 8}
        if country and country != "*":
            params["country"] = country.lower()
        data = self._get("https://api.mapbox.com/search/searchbox/v1/suggest", params)
        return [Suggestion(id=s["mapbox_id"], text=s.get("name", ""),
                           secondary=s.get("place_formatted") or s.get("full_address", ""))
                for s in data.get("suggestions", []) if s.get("mapbox_id")]

    def retrieve(self, provider_result_id: str, session_token: str) -> tuple[CanonicalAddress, CanonicalLocation | None]:
        data = self._get(f"https://api.mapbox.com/search/searchbox/v1/retrieve/{provider_result_id}",
                         {"session_token": session_token or "zoiko"})
        features = data.get("features") or []
        if not features:
            raise LocationUnavailable("mapbox retrieve returned nothing")
        return _mapbox_address(features[0]), _mapbox_location(features[0], place_id=provider_result_id)

    def _forward(self, address: CanonicalAddress) -> list[dict]:
        params = {"q": address.one_line()[:256], "limit": 3, "autocomplete": "false"}
        if address.country_code:
            params["country"] = address.country_code.lower()
        return self._get("https://api.mapbox.com/search/geocode/v6/forward", params).get("features") or []

    def validate(self, address: CanonicalAddress) -> ValidationResult:
        features = self._forward(address)
        if not features:
            return ValidationResult("UNRESOLVED", address, provider=self.code)
        top = features[0]
        props = top.get("properties") or {}
        feature_type = props.get("feature_type", "")
        confidence = (props.get("match_code") or {}).get("confidence", "")
        location = _mapbox_location(top)
        if feature_type in _AREA_TYPES:
            return _area_only(address, location, self.code)
        found = _mapbox_address(top)
        status = "VALIDATED" if feature_type == "address" and confidence in ("exact", "high") else "PARTIAL"
        if len(features) > 1 and confidence in ("low", "medium"):
            location.geocode_status = "AMBIGUOUS"
        return _merge_found(address, found, status, location, self.code)

    def geocode(self, address: CanonicalAddress) -> CanonicalLocation | None:
        features = self._forward(address)
        if not features:
            return None
        top = features[0]
        location = _mapbox_location(top)
        if (top.get("properties") or {}).get("feature_type", "") in _AREA_TYPES:
            location.precision = "APPROXIMATE"
        return location

    def reverse(self, latitude: float, longitude: float) -> CanonicalAddress | None:
        data = self._get("https://api.mapbox.com/search/geocode/v6/reverse",
                         {"latitude": latitude, "longitude": longitude, "limit": 1})
        features = data.get("features") or []
        return _mapbox_address(features[0]) if features else None


def _mapbox_address(feature: dict) -> CanonicalAddress:
    props = feature.get("properties") or {}
    ctx = props.get("context") or {}
    addr = ctx.get("address") or {}
    line1 = " ".join(p for p in (addr.get("address_number", ""), addr.get("street_name", "")) if p) \
        or (ctx.get("street") or {}).get("name", "") or (props.get("name") if props.get("feature_type") == "address" else "")
    return CanonicalAddress(
        address_line_1=line1 or "",
        address_line_2=(ctx.get("neighborhood") or {}).get("name", "") or (ctx.get("locality") or {}).get("name", ""),
        locality=(ctx.get("place") or {}).get("name", "") or (ctx.get("locality") or {}).get("name", ""),
        administrative_area=(ctx.get("region") or {}).get("name", ""),
        postal_code=(ctx.get("postcode") or {}).get("name", ""),
        country_code=((ctx.get("country") or {}).get("country_code") or "").upper(),
        formatted=props.get("full_address") or props.get("place_formatted", ""),
    )


def _mapbox_location(feature: dict, place_id: str = "") -> CanonicalLocation:
    props = feature.get("properties") or {}
    coords = props.get("coordinates") or {}
    if "latitude" in coords:
        lat, lng = float(coords["latitude"]), float(coords["longitude"])
    else:
        lng, lat = (float(v) for v in (feature.get("geometry") or {}).get("coordinates", [0, 0])[:2])
    precision = _MAPBOX_ACCURACY.get(coords.get("accuracy", ""), "")
    if not precision:
        precision = "STREET" if props.get("feature_type") == "street" else (
            "APPROXIMATE" if props.get("feature_type") in _AREA_TYPES else "INTERPOLATED")
    return CanonicalLocation(lat, lng, precision, "RESOLVED", place_id=place_id or props.get("mapbox_id", ""))


# -- HERE (secondary) ------------------------------------------------------------
# Section 3.2: Autocomplete / Lookup, Geocode and Reverse Geocode v7 with
# point-address vs interpolated distinctions.

_ISO3_TO_ISO2 = {"GBR": "GB", "IND": "IN", "USA": "US", "IRL": "IE", "AUS": "AU", "CAN": "CA", "ARE": "AE",
                 "SGP": "SG", "DEU": "DE", "FRA": "FR", "ESP": "ES", "ITA": "IT", "NLD": "NL", "PRT": "PT",
                 "NZL": "NZ", "ZAF": "ZA", "PAK": "PK", "BGD": "BD", "LKA": "LK", "NPL": "NP", "SAU": "SA",
                 "QAT": "QA", "MYS": "MY", "PHL": "PH", "JPN": "JP", "KOR": "KR", "CHN": "CN", "BRA": "BR",
                 "MEX": "MX"}
_ISO2_TO_ISO3 = {v: k for k, v in _ISO3_TO_ISO2.items()}


class HereLocationProvider(LocationProvider):
    code = "here"
    supports_suggest = True

    def __init__(self, api_key: str, timeout: float):
        self._key = api_key
        self._timeout = timeout

    def _get(self, url: str, params: dict) -> dict:
        try:
            response = httpx.get(url, params={**params, "apiKey": self._key}, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise LocationUnavailable(f"here request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            logger.warning("location: here %s returned HTTP %s", url.split("//", 1)[-1].split(".", 1)[0], response.status_code)
            raise LocationUnavailable(f"here returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise LocationUnavailable("here returned invalid JSON") from exc

    @staticmethod
    def _in(country: str) -> dict:
        iso3 = _ISO2_TO_ISO3.get((country or "").upper())
        return {"in": f"countryCode:{iso3}"} if iso3 else {}

    def suggest(self, query: str, country: str, session_token: str) -> list[Suggestion]:
        data = self._get("https://autocomplete.search.hereapi.com/v1/autocomplete",
                         {"q": query[:200], "limit": 8, "types": "address", **self._in(country)})
        return [Suggestion(id=i["id"], text=i.get("title", ""), secondary=(i.get("address") or {}).get("label", ""))
                for i in data.get("items", []) if i.get("id")]

    def retrieve(self, provider_result_id: str, session_token: str) -> tuple[CanonicalAddress, CanonicalLocation | None]:
        item = self._get("https://lookup.search.hereapi.com/v1/lookup", {"id": provider_result_id})
        if not item or "address" not in item:
            raise LocationUnavailable("here lookup returned nothing")
        return _here_address(item), _here_location(item, place_id=provider_result_id)

    def _geocode_items(self, address: CanonicalAddress) -> list[dict]:
        qq = ";".join(f"{k}={v}" for k, v in (("street", address.address_line_1), ("city", address.locality),
                                              ("state", address.administrative_area),
                                              ("postalCode", address.postal_code)) if v)
        params = {"qq": qq} if qq else {"q": address.one_line()}
        items = self._get("https://geocode.search.hereapi.com/v1/geocode",
                          {**params, "limit": 3, **self._in(address.country_code)}).get("items") or []
        if not items and qq:
            items = self._get("https://geocode.search.hereapi.com/v1/geocode",
                              {"q": address.one_line(), "limit": 3, **self._in(address.country_code)}).get("items") or []
        return items

    def validate(self, address: CanonicalAddress) -> ValidationResult:
        items = self._geocode_items(address)
        if not items:
            return ValidationResult("UNRESOLVED", address, provider=self.code)
        top = items[0]
        location = _here_location(top)
        result_type = top.get("resultType", "")
        if result_type in ("locality", "administrativeArea", "postalCodePoint"):
            return _area_only(address, location, self.code)
        score = float((top.get("scoring") or {}).get("queryScore") or 0)
        status = "VALIDATED" if result_type in ("houseNumber", "place") and score >= 0.85 else "PARTIAL"
        if len(items) > 1 and score < 0.85:
            location.geocode_status = "AMBIGUOUS"
        return _merge_found(address, _here_address(top), status, location, self.code)

    def geocode(self, address: CanonicalAddress) -> CanonicalLocation | None:
        items = self._geocode_items(address)
        return _here_location(items[0]) if items else None

    def reverse(self, latitude: float, longitude: float) -> CanonicalAddress | None:
        items = self._get("https://revgeocode.search.hereapi.com/v1/revgeocode",
                          {"at": f"{latitude},{longitude}", "limit": 1}).get("items") or []
        return _here_address(items[0]) if items else None


def _here_address(item: dict) -> CanonicalAddress:
    a = item.get("address") or {}
    return CanonicalAddress(
        address_line_1=" ".join(p for p in (a.get("houseNumber", ""), a.get("street", "")) if p),
        address_line_2=a.get("district", "") or a.get("subdistrict", ""),
        locality=a.get("city", "") or a.get("county", ""),
        administrative_area=a.get("state", ""),
        postal_code=a.get("postalCode", ""),
        country_code=_ISO3_TO_ISO2.get(a.get("countryCode", ""), (a.get("countryCode") or "")[:2]),
        formatted=a.get("label", ""),
    )


def _here_location(item: dict, place_id: str = "") -> CanonicalLocation:
    pos = item.get("position") or {}
    result_type = item.get("resultType", "")
    if result_type == "houseNumber":
        precision = "INTERPOLATED" if item.get("houseNumberType") == "interpolated" else "ROOFTOP"
    elif result_type == "place":
        precision = "PARCEL"
    elif result_type in ("street", "intersection"):
        precision = "STREET"
    else:
        precision = "APPROXIMATE"
    return CanonicalLocation(float(pos.get("lat", 0)), float(pos.get("lng", 0)), precision, "RESOLVED",
                             place_id=place_id or item.get("id", ""))


# -- shared result shaping ----------------------------------------------------------

def _area_only(address: CanonicalAddress, location: CanonicalLocation, provider: str) -> ValidationResult:
    """Only the town / postal area resolved (rural, new-build or local
    addressing -- Section 20): keep the host's own address, centre the map
    on the area at APPROXIMATE precision and let evidence / review decide.
    Never a rejection (Section 7.1: coverage gaps don't auto-reject)."""
    location.precision = "APPROXIMATE"
    return ValidationResult("PARTIAL", CanonicalAddress(**{**address.as_dict(), "formatted": address.one_line()}),
                            None, location, provider=provider)


def _merge_found(address: CanonicalAddress, found: CanonicalAddress, status: str, location: CanonicalLocation,
                 provider: str) -> ValidationResult:
    canonical = CanonicalAddress(
        address_line_1=address.address_line_1 or found.address_line_1,
        address_line_2=address.address_line_2,
        subpremise=address.subpremise,
        locality=address.locality or found.locality,
        administrative_area=address.administrative_area or found.administrative_area,
        postal_code=address.postal_code or found.postal_code,
        country_code=(found.country_code or address.country_code).upper(),
        formatted=found.formatted,
    )
    suggestion = None
    if found.postal_code and address.postal_code and \
            found.postal_code.replace(" ", "").lower() != address.postal_code.replace(" ", "").lower():
        suggestion = CanonicalAddress(**{**canonical.as_dict(), "postal_code": found.postal_code})
    return ValidationResult(status, canonical, suggestion, location, provider=provider)


class NoLocationProvider(LocationProvider):
    """No provider credentials configured: every call is unavailable, so the
    host completes the manual structured-address path and evidence/review
    decides (Section 3.2 "Manual / registry-led path")."""
    code = "none"

    def validate(self, address: CanonicalAddress) -> ValidationResult:
        raise LocationUnavailable("no location provider configured")

    def geocode(self, address: CanonicalAddress) -> CanonicalLocation | None:
        raise LocationUnavailable("no location provider configured")

    def reverse(self, latitude: float, longitude: float) -> CanonicalAddress | None:
        raise LocationUnavailable("no location provider configured")


# -- routing (Sections 3.3, 13 resilience) -------------------------------------------

def _build(code: str) -> LocationProvider | None:
    timeout = settings.geocoding_timeout_seconds
    if code == "google" and settings.google_maps_api_key:
        return GoogleLocationProvider(settings.google_maps_api_key, timeout)
    if code == "mapbox" and settings.mapbox_access_token:
        return MapboxLocationProvider(settings.mapbox_access_token, timeout)
    if code == "here" and settings.here_api_key:
        return HereLocationProvider(settings.here_api_key, timeout)
    return None


def provider_chain() -> list[LocationProvider]:
    """Primary (Google by default) then the pre-qualified secondaries
    (Mapbox, HERE) that have credentials configured."""
    order = [(settings.location_provider or "google").lower()]
    if settings.location_fallback_enabled:
        order += [c.strip().lower() for c in (settings.location_fallback_providers or "").split(",") if c.strip()]
    chain, seen = [], set()
    for code in order:
        if code in seen or code == "none":
            continue
        seen.add(code)
        provider = _build(code)
        if provider is not None:
            chain.append(provider)
    return chain


def primary_provider() -> LocationProvider:
    chain = provider_chain()
    return chain[0] if chain else NoLocationProvider()


def _empty(call: str, result) -> bool:
    if call == "validate":
        return result.status == "UNRESOLVED"
    return result is None


def _with_fallback(call: str, *args):
    """Runs `call` down the provider chain: an outage, quota error or a
    coverage gap (nothing found) moves to the next provider. Returns
    (result, provider_code); raises LocationUnavailable only when every
    configured provider is down (or none is configured). A provider failure
    never approves anything -- callers route to manual entry / review."""
    chain = provider_chain()
    if not chain:
        raise LocationUnavailable("no location provider configured")
    last, last_code, failures = None, "", 0
    for provider in chain:
        try:
            result = getattr(provider, call)(*args)
        except LocationUnavailable:
            failures += 1
            continue
        if not _empty(call, result):
            return result, provider.code
        if last is None:
            last, last_code = result, provider.code
    if last is None and failures:
        raise LocationUnavailable("all location providers unavailable")
    return last, last_code


def suggest(query: str, country: str, session_token: str) -> list[Suggestion]:
    """Autocomplete from the first provider that offers it; ids are tagged
    with the provider so retrieve() goes back to the same one."""
    if len(query.strip()) < 3:
        return []
    for provider in provider_chain():
        if not provider.supports_suggest:
            continue
        try:
            items = provider.suggest(query.strip(), country, session_token)
        except LocationUnavailable:
            continue
        return [Suggestion(id=f"{provider.code}:{s.id}", text=s.text, secondary=s.secondary) for s in items]
    return []


def retrieve(provider_result_id: str, session_token: str) -> tuple[CanonicalAddress, CanonicalLocation | None, str]:
    code, _, raw_id = provider_result_id.partition(":")
    provider = _build(code) if raw_id else None
    if provider is None:
        provider, raw_id = primary_provider(), provider_result_id
    address, location = provider.retrieve(raw_id, session_token)
    return address, location, provider.code


def validate(address: CanonicalAddress) -> ValidationResult:
    result, code = _with_fallback("validate", address)
    result.provider = code
    return result


def geocode(address: CanonicalAddress) -> tuple[CanonicalLocation | None, str]:
    return _with_fallback("geocode", address)


def reverse(latitude: float, longitude: float) -> tuple[CanonicalAddress | None, str]:
    return _with_fallback("reverse", latitude, longitude)


def capabilities() -> dict:
    """What the host UI may offer. The browser map uses its own restricted
    web-client key (Section 4), never these server credentials."""
    chain = provider_chain()
    return {"provider": chain[0].code if chain else "none", "providers": [p.code for p in chain],
            "autocomplete": any(p.supports_suggest for p in chain), "configured": bool(chain)}
