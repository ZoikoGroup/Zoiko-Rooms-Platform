"""Licensed listing APIs for external discovery (ZR-AI-SEARCH-001 Section 6.1
Tier B: "Licensed search/data API -- use within provider terms").

Each adapter only knows how to call one provider and turn its response into
``ExternalCandidate`` records. Whether a provider may be called, displayed or
used for outreach is decided elsewhere and fails closed:

* an API key in settings only makes the adapter callable;
* the orchestrator calls it only when the source's Source Rights Registry row
  is ACTIVE + legal + security approved, external.search_fallback is on, and
  the broker's rights + SSRF gate passes for the configured URL;
* candidates become consumer cards only when the registry permits masked
  display, and only with the fields the registry permits.

Raw responses never leave this module: candidates carry the minimum fields
(Section 6.3), and source URL / exact address / provider contact are kept in
server-side-only attributes that the orchestrator encrypts at rest.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExternalCandidate:
    source_id: str
    external_id: str
    city: str | None = None
    region: str | None = None
    country: str | None = None
    room_type: str | None = None
    bedrooms: int | None = None
    advertised_price_minor: int | None = None
    currency: str | None = None
    price_period: str | None = None  # MONTH | WEEK
    # Server-side only: never put on a card or sent to the LLM.
    source_url: str | None = None
    exact_address: str | None = None
    provider_name: str | None = None
    provider_contact: str | None = None

    @property
    def dedupe_hash(self) -> str:
        return hashlib.sha256(f"{self.source_id}:{self.external_id}".encode()).hexdigest()

    @property
    def rent_monthly(self) -> int | None:
        """Whole-unit monthly figure for the card's advertised price."""
        if self.advertised_price_minor is None:
            return None
        amount = self.advertised_price_minor / 100
        if self.price_period == "WEEK":
            amount = amount * 52 / 12
        return int(round(amount))


class ListingProvider(Protocol):
    source_id: str
    countries: frozenset[str]

    @property
    def url(self) -> str: ...

    def configured(self) -> bool: ...

    def search(self, client: httpx.Client, *, city: str, limit: int) -> list[ExternalCandidate]: ...


# Country names a user (or the assistant) may type, mapped to ISO alpha-2.
_COUNTRY_ALIASES = {
    "US": "US", "USA": "US", "U.S.": "US", "U.S.A.": "US", "AMERICA": "US",
    "UNITED STATES": "US", "UNITED STATES OF AMERICA": "US",
    "GB": "GB", "UK": "GB", "U.K.": "GB", "GBR": "GB", "UNITED KINGDOM": "GB",
    "GREAT BRITAIN": "GB", "BRITAIN": "GB", "ENGLAND": "GB", "SCOTLAND": "GB",
    "WALES": "GB", "NORTHERN IRELAND": "GB",
    "AU": "AU", "AUS": "AU", "AUSTRALIA": "AU",
    "IN": "IN", "IND": "IN", "INDIA": "IN", "BHARAT": "IN",
}


# Section 6.3: source text is untrusted. Labels (room type, city, region) are
# kept only when they look like a plain label -- letters, spaces and a few
# separators, short, and with no instruction-like or link-like content.
_LABEL = re.compile(r"^[\w][\w .,'()/&-]{0,59}$", re.UNICODE)
_INJECTION = re.compile(
    r"\b(?:ignore|disregard|instruction|instructions|system|prompt|assistant|override|print|http|www)\b",
    re.IGNORECASE,
)


def clean_label(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text or not _LABEL.match(text) or _INJECTION.search(text):
        return None
    return text


def normalize_country(country: str | None) -> str | None:
    if not country:
        return None
    return _COUNTRY_ALIASES.get(country.strip().upper())


def _price_minor(value: Any) -> int | None:
    try:
        return int(round(float(value) * 100)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _first_contact(*parties: dict[str, Any] | None) -> str | None:
    for party in parties:
        if not party:
            continue
        for key in ("email", "phone"):
            if party.get(key):
                return str(party[key])
    return None


class RentCastProvider:
    """RentCast long-term rental listings (US only). Free plan: 50 requests a
    month, so results are cached per the source's registry TTL."""

    source_id = "rentcast"
    countries = frozenset({"US"})

    @property
    def url(self) -> str:
        return settings.rentcast_api_url

    def configured(self) -> bool:
        return bool(settings.rentcast_api_key)

    def search(self, client: httpx.Client, *, city: str, limit: int) -> list[ExternalCandidate]:
        res = client.get(
            self.url,
            params={"city": city, "status": "Active", "limit": limit},
            headers={"X-Api-Key": settings.rentcast_api_key, "Accept": "application/json"},
        )
        res.raise_for_status()
        rows = res.json()
        out: list[ExternalCandidate] = []
        for row in rows if isinstance(rows, list) else []:
            if not row.get("id"):
                continue
            out.append(
                ExternalCandidate(
                    source_id=self.source_id,
                    external_id=str(row["id"]),
                    city=clean_label(row.get("city")),
                    region=clean_label(row.get("state")),
                    country="US",
                    room_type=clean_label(row.get("propertyType")),
                    bedrooms=row.get("bedrooms"),
                    advertised_price_minor=_price_minor(row.get("price")),
                    currency="USD",
                    price_period="MONTH",
                    exact_address=row.get("formattedAddress"),
                    provider_name=(row.get("listingAgent") or {}).get("name")
                    or (row.get("listingOffice") or {}).get("name"),
                    provider_contact=_first_contact(row.get("listingAgent"), row.get("listingOffice")),
                )
            )
        return out


class DomainProvider:
    """Domain residential rental listings (Australia). Domain's API terms
    require attribution and a way to view the original listing, so the
    registry row should keep masked display off; listings are then only
    registered internally (Section 15.4 register_internal_opportunity)."""

    source_id = "domain_au"
    countries = frozenset({"AU"})

    @property
    def url(self) -> str:
        return settings.domain_api_url

    def configured(self) -> bool:
        return bool(settings.domain_api_key)

    def search(self, client: httpx.Client, *, city: str, limit: int) -> list[ExternalCandidate]:
        res = client.post(
            self.url,
            json={
                "listingType": "Rent",
                "pageSize": limit,
                "locations": [{"suburb": city, "includeSurroundingSuburbs": False}],
            },
            headers={"X-Api-Key": settings.domain_api_key, "Accept": "application/json"},
        )
        res.raise_for_status()
        rows = res.json()
        out: list[ExternalCandidate] = []
        for item in rows if isinstance(rows, list) else []:
            listing = item.get("listing") or {}
            if not listing.get("id"):
                continue
            details = listing.get("propertyDetails") or {}
            advertiser = listing.get("advertiser") or {}
            contacts = advertiser.get("contacts") or []
            slug = listing.get("listingSlug")
            out.append(
                ExternalCandidate(
                    source_id=self.source_id,
                    external_id=str(listing["id"]),
                    city=clean_label(details.get("suburb")),
                    region=clean_label(details.get("state")),
                    country="AU",
                    room_type=clean_label(details.get("propertyType")),
                    bedrooms=details.get("bedrooms"),
                    advertised_price_minor=_price_minor((listing.get("priceDetails") or {}).get("price")),
                    currency="AUD",
                    price_period="WEEK",
                    source_url=f"https://www.domain.com.au/{slug}" if slug else None,
                    exact_address=details.get("displayableAddress"),
                    provider_name=advertiser.get("name"),
                    provider_contact=_first_contact(*(c for c in contacts if isinstance(c, dict))),
                )
            )
        return out


PROVIDERS: dict[str, ListingProvider] = {
    p.source_id: p for p in (RentCastProvider(), DomainProvider())
}

# Per-process result cache: (source_id, city) -> (expires_at, candidates).
# Bounded by the source's registry cache_ttl_seconds (Section 6.3 refresh/expire).
_cache: dict[tuple[str, str], tuple[float, list[ExternalCandidate]]] = {}


def fetch_candidates(
    provider: ListingProvider, *, city: str, limit: int, ttl_seconds: int
) -> list[ExternalCandidate]:
    """Call one provider (or reuse a fresh cached result). Network or
    provider errors return no candidates rather than failing the search."""
    key = (provider.source_id, city.strip().lower())
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1][:limit]
    try:
        with httpx.Client(
            timeout=settings.external_listing_timeout_seconds, follow_redirects=False
        ) as client:
            candidates = provider.search(client, city=city, limit=limit)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("external listing provider %s failed: %s", provider.source_id, type(exc).__name__)
        return []
    if ttl_seconds > 0:
        _cache[key] = (now + ttl_seconds, candidates)
    return candidates[:limit]


def clear_cache() -> None:
    _cache.clear()
    _web_cache.clear()


# -- Web search over approved websites (Brave Search API) ----------------------

COUNTRY_NAMES = {"GB": "UK", "US": "USA", "AU": "Australia", "IN": "India"}


@dataclass(frozen=True)
class WebHit:
    """One search result on an approved website. The URL is kept (encrypted
    later) plus, at most, an advertised rent and bedroom count parsed out of
    the snippet. The snippet text itself is discarded: it is untrusted, can
    carry contact details or instructions, and would let a renter find the
    source."""

    domain: str
    url: str
    price_minor: int | None = None
    currency: str | None = None
    price_period: str | None = None
    room_type: str | None = None


# Market -> (regex for its currency marker, ISO currency).
_MARKET_CURRENCY = {
    "GB": (r"£", "GBP"),
    "US": (r"\$", "USD"),
    "IN": (r"₹|\brs\.?|\binr\b", "INR"),
}
_PERIOD_WORDS = {
    "WEEK": r"p\s?/\s?w|p\.?w\.?|per\s+week|/\s?w(?:ee)?k|a\s+week|weekly",
    "MONTH": r"p\s?/\s?m|p\.?c\.?m\.?|per\s+(?:calendar\s+)?month|/\s?mo(?:nth)?|a\s+month|monthly|onwards\s*/\s*month",
}
_BEDROOMS = re.compile(r"\b(?:bedrooms?\s*[:\-]?\s*(\d{1,2})|(\d{1,2})\s*-?\s*bed(?:room)?s?)\b", re.IGNORECASE)
_BEDROOM_RANGE = re.compile(r"\b(\d)\s*[-–]\s*(\d)\s*-?\s*bed(?:room)?s?\b", re.IGNORECASE)
_STUDIO = re.compile(r"\bstudio\b", re.IGNORECASE)
# "Starting at $1,051", "rent from £850", "from ₹9,396": the lowest advertised
# rent, with no period stated. Recorded as MONTH_FROM and shown as "from".
_FROM_WORDS = r"(?:starting\s+(?:at|from)|starts?\s+(?:at|from)|(?:base\s+)?rents?\s+(?:starts?\s+)?(?:at|from)|from)"
# Zoiko Rooms only lists rooms to live in. Search hits that are anything else
# are dropped: sales, offices, shops and other commercial space, work studios,
# studio flats, or editorial pages. Matched against title + snippet text,
# where "office" alone is too common ("near your office") to count.
_NON_RENTAL_TEXT = re.compile(
    r"\bfor\s+sale\b|\b(?:flex|serviced|shared)\s+offices?\b|\boffices?\s+(?:space|suites?|to\s+let|for\s+rent)\b"
    r"|\bcommercial\s+(?:propert(?:y|ies)|space|units?)\b|\bco-?working\b|\bwarehouses?\b|\bgodowns?\b"
    r"|\bindustrial\s+units?\b|\bretail\s+(?:space|units?|shops?)\b|\bshowrooms?\b"
    r"|\bshops?\s+(?:for\s+rent|to\s+(?:let|rent))\b|\b(?:shop|salon|restaurant|kiosk)\s+space\b"
    r"|\b(?:photo(?:graphy)?|dance|yoga|recording|music|art|film|podcast|fitness|pilates|tattoo)\s+studios?\b"
    r"|\bstudio\s+(?:flats?|apartments?|apts?|units?|homes?)\b|\bstudios?\s+(?:for\s+rent|to\s+(?:let|rent))\b",
    re.IGNORECASE,
)
_NON_RENTAL_PATH = re.compile(
    r"/(?:blog|news|insights?|for-sale|properties-for-sale|sales?|commercial|rent-office|offices?|shops?"
    r"|retail|studios?|studio-(?:flats?|apartments?))(?:/|-|$)",
    re.IGNORECASE,
)
# A provider's own property-type label ("Retail", "Studio", "Office") that is
# not a room to live in. Labels are short, so single words are safe here.
_NOT_A_HOME_LABEL = re.compile(
    r"\b(?:studios?|shops?|retail|showrooms?|salons?|restaurants?|caf[eé]s?|kiosks?|offices?|commercial"
    r"|co-?working|warehouses?|godowns?|industrial|land|parking|garages?|storage|hotel)\b",
    re.IGNORECASE,
)


def is_home_to_live_in(room_type: str | None, bedrooms: int | None = None) -> bool:
    """False for studios (flats or work studios), shops and other commercial
    space; True otherwise, including when the type is unknown."""
    if bedrooms is not None and bedrooms == 0:
        return False  # 0 bedrooms = studio
    return not (room_type and _NOT_A_HOME_LABEL.search(room_type))
# Plausible monthly rent bounds (whole units) per market; anything outside is
# treated as not-a-rent (sale price, deposit, typo).
_MONTHLY_BOUNDS = {"GB": (150, 20000), "US": (300, 25000), "IN": (2000, 500000)}


def extract_listing_facts(snippet: str, market: str) -> dict[str, Any]:
    """Pull an advertised rent and a bedroom count out of untrusted search
    snippet text. A price counts only with an explicit rental period, in the
    market's own currency, within plausible bounds; otherwise no price.
    Nothing else from the text is returned."""
    facts: dict[str, Any] = {}
    if not snippet or market not in _MARKET_CURRENCY:
        return facts
    symbol, currency = _MARKET_CURRENCY[market]
    for period, words in _PERIOD_WORDS.items():
        pattern = re.compile(
            # Amounts: 1,275 / 26,058 / Indian lakh style 1,20,000 / plain 950.
            rf"(?:{symbol})\s?(\d{{1,3}}(?:,\d{{2,3}})+|\d{{2,6}})(?:\.\d{{2}})?\s*(?:{words})",
            re.IGNORECASE,
        )
        match = pattern.search(snippet)
        if not match:
            continue
        amount = int(match.group(1).replace(",", ""))
        monthly = amount * 52 / 12 if period == "WEEK" else amount
        low, high = _MONTHLY_BOUNDS[market]
        if low <= monthly <= high:
            facts.update(price_minor=amount * 100, currency=currency, price_period=period)
            break
    if "price_minor" not in facts:
        # No period stated, but an explicit "starting at / from" rent: kept as a
        # monthly lower bound only when it is a plausible monthly rent.
        match = re.search(
            rf"{_FROM_WORDS}\s*(?:{symbol})\s?(\d{{1,3}}(?:,\d{{2,3}})+|\d{{2,6}})(?:\.\d{{2}})?",
            snippet,
            re.IGNORECASE,
        )
        if match:
            amount = int(match.group(1).replace(",", ""))
            low, high = _MONTHLY_BOUNDS[market]
            if low <= amount <= high:
                facts.update(price_minor=amount * 100, currency=currency, price_period="MONTH_FROM")
    # Bedrooms win over "studio" when both appear ("studio, 1-2 bedrooms"):
    # the listing then has rooms to live in. Studio-only stays "Studio" and
    # is dropped by _web_hit.
    beds_range = _BEDROOM_RANGE.search(snippet)
    beds = _BEDROOMS.search(snippet)
    if beds_range and 0 < int(beds_range.group(1)) < int(beds_range.group(2)) < 10:
        facts["room_type"] = f"{beds_range.group(1)}-{beds_range.group(2)} bedrooms"
    elif beds and 0 < int(beds.group(1) or beds.group(2)) < 10:
        n = int(beds.group(1) or beds.group(2))
        facts["room_type"] = f"{n} bedroom" + ("s" if n > 1 else "")
    elif _STUDIO.search(snippet):
        facts["room_type"] = "Studio"
    return facts


def _host_matches(host: str, domain: str) -> bool:
    host = host.lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


def _web_hit(url: str, text: str, domains: list[str], market: str) -> WebHit | None:
    """A search result as a WebHit when it is on an approved domain and looks
    like a home to rent; None otherwise. `text` (title + snippet) is only
    scanned here and then discarded."""
    if not url.startswith(("http://", "https://")):
        return None
    parsed = httpx.URL(url)
    domain = next((d for d in domains if parsed.host and _host_matches(parsed.host, d)), None)
    if not domain or _NON_RENTAL_PATH.search(parsed.path) or _NON_RENTAL_TEXT.search(text):
        return None
    facts = extract_listing_facts(text, market)
    if not is_home_to_live_in(facts.get("room_type")):
        return None
    return WebHit(domain=domain, url=url, **facts)


class BraveWebSearch:
    """Brave Search API restricted to approved sites (Section 6.1 Tier B
    search API + Tier C approved websites). Zoiko Rooms never fetches the
    result pages: the search API is the only request made."""

    source_id = "brave_web"

    @property
    def url(self) -> str:
        return settings.brave_search_api_url

    def configured(self) -> bool:
        return bool(settings.brave_search_api_key)

    def search(
        self, client: httpx.Client, *, city: str, market: str, domains: list[str], limit: int
    ) -> list[WebHit]:
        sites = " OR ".join(f"site:{d}" for d in domains)
        query = f"room to rent {city} {COUNTRY_NAMES.get(market, market)} ({sites})"
        res = client.get(
            self.url,
            params={"q": query, "count": min(max(limit, 1), 20), "safesearch": "strict"},
            headers={"X-Subscription-Token": settings.brave_search_api_key, "Accept": "application/json"},
        )
        res.raise_for_status()
        results = ((res.json() or {}).get("web") or {}).get("results") or []
        hits: list[WebHit] = []
        for item in results:
            item = item or {}
            text = f"{item.get('title') or ''} {item.get('description') or ''}"
            hit = _web_hit(str(item.get("url") or ""), text, domains, market)
            if hit:
                hits.append(hit)
        return hits[:limit]


class ParallelWebSearch:
    """Parallel Search API restricted to approved sites via
    source_policy.include_domains. Same contract as BraveWebSearch: only the
    search API is called; only result URLs and the facts extract_listing_facts
    parses out (rent, bedrooms) are kept -- excerpts and titles are discarded)."""

    source_id = "parallel_web"

    @property
    def url(self) -> str:
        return settings.parallel_search_api_url

    def configured(self) -> bool:
        return bool(settings.parallel_api_key)

    def search(
        self, client: httpx.Client, *, city: str, market: str, domains: list[str], limit: int
    ) -> list[WebHit]:
        country = COUNTRY_NAMES.get(market, market)
        res = client.post(
            self.url,
            json={
                "objective": (
                    f"Rooms to live in currently advertised to rent in {city}, {country}: private or shared "
                    "rooms in flats or houses, PGs and co-living. Not studio flats, shops, offices or other "
                    "commercial space."
                ),
                "search_queries": [f"room to rent {city}", f"{city} rooms to let"],
                "mode": "fast",
                "max_chars_total": 4000,
                "advanced_settings": {
                    "source_policy": {"include_domains": domains},
                    # Enough to reach the advertised rent; the text itself is discarded.
                    "excerpt_settings": {"max_chars_per_result": 400},
                    "location": market,
                    "max_results": min(max(limit, 1), 20),
                },
            },
            headers={"x-api-key": settings.parallel_api_key, "Accept": "application/json"},
        )
        res.raise_for_status()
        results = (res.json() or {}).get("results") or []
        hits: list[WebHit] = []
        for item in results:
            item = item or {}
            text = " ".join([str(item.get("title") or ""), *(str(x) for x in (item.get("excerpts") or []))])
            hit = _web_hit(str(item.get("url") or ""), text, domains, market)
            if hit:
                hits.append(hit)
        return hits[:limit]


class TinyFishWebSearch:
    """TinyFish Search API. It has no domain-filter parameter, so the query
    carries site: operators and every result is still checked against the
    approved domains. Same contract as the others: only the search API is
    called; only result URLs and the facts extract_listing_facts parses out
    (rent, bedrooms) are kept -- titles and snippets are discarded."""

    source_id = "tinyfish_web"

    @property
    def url(self) -> str:
        return settings.tinyfish_search_api_url

    def configured(self) -> bool:
        return bool(settings.tinyfish_api_key)

    def search(
        self, client: httpx.Client, *, city: str, market: str, domains: list[str], limit: int
    ) -> list[WebHit]:
        sites = " OR ".join(f"site:{d}" for d in domains)
        res = client.get(
            self.url,
            params={"query": f"rooms to rent {city} {sites}", "location": market},
            headers={"X-API-Key": settings.tinyfish_api_key, "Accept": "application/json"},
        )
        res.raise_for_status()
        results = (res.json() or {}).get("results") or []
        hits: list[WebHit] = []
        for item in results:
            item = item or {}
            text = f"{item.get('title') or ''} {item.get('snippet') or ''}"
            hit = _web_hit(str(item.get("url") or ""), text, domains, market)
            if hit:
                hits.append(hit)
        return hits[:limit]


WEB_SEARCH_PROVIDERS = {"tinyfish": TinyFishWebSearch(), "parallel": ParallelWebSearch(), "brave": BraveWebSearch()}
WebSearchProvider = BraveWebSearch | ParallelWebSearch | TinyFishWebSearch


def web_search_providers() -> list[WebSearchProvider]:
    """Every internet-search provider with an API key, in the order to try
    them: WEB_SEARCH_PROVIDER first when set, then TinyFish, Parallel, Brave.
    The later ones are fallbacks for when an earlier one fails or finds nothing."""
    configured = [p for p in WEB_SEARCH_PROVIDERS.values() if p.configured()]
    chosen = WEB_SEARCH_PROVIDERS.get((settings.web_search_provider or "").strip().lower())
    if chosen in configured:
        configured.remove(chosen)
        configured.insert(0, chosen)
    return configured


def web_search_provider() -> WebSearchProvider | None:
    """The first provider web_search_providers() would try."""
    return next(iter(web_search_providers()), None)

_web_cache: dict[tuple[str, str, str, tuple[str, ...]], tuple[float, list[WebHit]]] = {}


def fetch_web_hits(
    provider: WebSearchProvider,
    *,
    city: str,
    market: str,
    domains: list[str],
    limit: int,
    ttl_seconds: int,
) -> list[WebHit]:
    """Search approved sites for rentals in a city (or reuse a fresh cached
    result). Errors return no hits rather than failing the search."""
    if not domains:
        return []
    key = (provider.source_id, city.strip().lower(), market, tuple(sorted(domains)))
    now = time.monotonic()
    hit = _web_cache.get(key)
    if hit and hit[0] > now:
        return hit[1][:limit]
    try:
        with httpx.Client(
            timeout=settings.external_listing_timeout_seconds, follow_redirects=False
        ) as client:
            hits = provider.search(client, city=city, market=market, domains=domains, limit=limit)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("web search failed: %s", type(exc).__name__)
        return []
    if ttl_seconds > 0:
        _web_cache[key] = (now + ttl_seconds, hits)
    return hits[:limit]
