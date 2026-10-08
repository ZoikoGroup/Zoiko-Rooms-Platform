"""Search orchestrator -- deterministic internal-first waterfall
(ZR-AI-SEARCH-001 SS-1).

Rules:
1. Internal verified inventory is always searched first.
2. External fallback is ONLY attempted when internal matches == 0.
3. Every external source must be explicitly rights-allowed; anything
   unknown/unlisted is BLOCKED (fail closed).
4. External cards are masked via the anti-circumvention sanitizer.

Section 5.1 qualifying-match gate + Section 5.2 internal ranking are enforced
in ``_qualifying_internal``/``_rank_internal`` (publication + risk state, room
type + objective-amenity filters, availability-freshness downgrade, and a
stable deterministic tie-break).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.external_search import ExternalOpportunity
from app.schemas.external_search import (
    ExternalCard,
    ExternalCardResult,
    ExternalDiscoveryResult,
    SearchState,
)
from app.services.anti_circumvention import sanitizer
from app.core.config import settings
from app.services.audit_ext import log_external_search_event
from app.services.external_broker import BrokerAccessError, broker
from app.services.external_providers import (
    PROVIDERS,
    WEB_SEARCH_PROVIDERS,
    ExternalCandidate,
    ListingProvider,
    fetch_candidates,
    fetch_web_hits,
    normalize_country,
    web_search_provider,
)
from app.services.external_search_crypto import encrypt_optional
from app.services.feature_flags import is_enabled
from app.services.source_rights_registry import registry

logger = logging.getLogger(__name__)

_SENTINEL_TITLE = "[External listing — masked]"

EXTERNAL_SEARCH_FLAG = "external.search_fallback"

# Section 7.1 approved assistant pattern (internal results).
INTERNAL_DISCLOSURE = (
    "I found Zoiko Rooms listings that match your search. These results come from "
    "Zoiko Rooms inventory. Availability can change, so check the listing's latest "
    "confirmation before proceeding."
)
# Section 7.2 mandatory disclosure (external fallback), verbatim.
EXTERNAL_DISCLOSURE = (
    "No matching Zoiko Rooms listings were found. We found potential room listings "
    "from approved external web sources. These are not Zoiko Rooms listings and have "
    "not been verified by Zoiko Rooms. Availability, price, property details and the "
    "provider's authority may have changed or may be inaccurate. You can ask Zoiko "
    "Rooms to contact the provider on your behalf."
)
# Section 14: no internal match and nothing displayable externally.
NO_MATCH_DISCLOSURE = (
    "No matching Zoiko Rooms listings were found for this search. Try widening your "
    "dates, budget or filters."
)

# SRCH-14 fair-housing guard: submissions describing a protected class never
# reach external fallback. Matched as whole words against the free-text query.
_PROTECTED_CLASS_TERMS = (
    "christian",
    "muslim",
    "jewish",
    "hindu",
    "atheist",
    "white",
    "black",
    "hispanic",
    "latino",
    "asian",
    "male only",
    "female only",
    "men only",
    "women only",
    "no children",
    "no kids",
    "single parents",
    "married",
    "disabled",
    "wheelchair users",
    "age 50",
    "young graduate",
)


def _fair_housing_block(city: str | None, q: str | None) -> str | None:
    """Return a reason string when the query encodes a protected characteristic,
    else None. `q` is matched on word boundaries; the city must match exactly."""
    text = (q or "").lower()
    words = text.split()
    for term in _PROTECTED_CLASS_TERMS:
        if " " in term:
            if term in text:
                return f"protected_class:{term}"
        elif any(
            w == term or w == f"{term}s" or w == f"{term}es" or w.endswith(f"{term}'s")
            for w in words
        ):
            # matches the bare word plus simple inflections (e.g. muslims) but
            # never a prefix of unrelated words (e.g. Whitechapel).
            return f"protected_class:{term}"
    if city and any(w.lower() in _PROTECTED_CLASS_TERMS for w in city.split()):
        return "protected_class:city"
    return None


@dataclass
class SearchQuery:
    q: str | None = None
    city: str | None = None
    country: str | None = None
    min_price: int | None = None
    max_price: int | None = None
    move_in_from: date | None = None
    move_in_to: date | None = None
    room_type: str | None = None
    objective_filters: list[str] = field(default_factory=list)
    limit_internal: int = 20
    limit_external: int = 10


@dataclass
class OrchestratorResult:
    discovery: ExternalDiscoveryResult
    internal_results: list[dict[str, Any]] = field(default_factory=list)
    external_raw: list[dict[str, Any]] = field(default_factory=list)
    # Server-side only, aligned with discovery.external_matches: the source
    # record behind each card (None for a dev placeholder). Never serialized.
    external_private: list[Any] = field(default_factory=list)


class SearchOrchestrator:
    """Owns the canonical state transitions for a search.

    state machine:
      internal matches > 0 -> INTERNAL_VERIFIED
      internal == 0, eligible sources -> EXTERNAL_FALLBACK_ELIGIBLE -> EXTERNAL_DISCOVERED
      internal == 0, no eligible sources -> BLOCKED
    """

    def search(
        self,
        db: Session,
        query: SearchQuery,
        correlation_id: str = "",
        actor_id: int | None = None,
    ) -> OrchestratorResult:
        fair_housing = _fair_housing_block(query.city, query.q)
        if fair_housing:
            blocked = ExternalDiscoveryResult(
                state=SearchState.BLOCKED,
                internal_matches=0,
                external_matches=[],
                fallback_triggered=False,
                consent_required=False,
                disclosure_text=(
                    "This search was not run because it described a protected "
                    "characteristic. Zoiko Rooms listings are open to everyone."
                ),
                guardrail_notes=["FAIR_HOUSING: protected characteristic rejected", fair_housing],
            )
            log_external_search_event(
                db,
                action="search_external.fair_housing_blocked",
                resource_type="search",
                resource_id=correlation_id or "search",
                correlation_id=correlation_id,
                reason=fair_housing,
            )
            return OrchestratorResult(discovery=blocked)

        internal = self._search_internal(db, query)
        # The precedence decision uses the full qualifying count, never the
        # display-truncated list: a small/zero limit must not open the external
        # fallback while internal matches exist (SRCH-01).
        qualifying_count = len(internal["rows"])
        internal_rows = internal["rows"][: max(1, query.limit_internal or 0)]

        if qualifying_count > 0:
            result = OrchestratorResult(
                discovery=ExternalDiscoveryResult(
                    state=SearchState.INTERNAL_VERIFIED,
                    internal_matches=qualifying_count,
                    external_matches=[],
                    fallback_triggered=False,
                    consent_required=False,
                    disclosure_text=INTERNAL_DISCLOSURE,
                    guardrail_notes=["INTERNAL_FIRST: returned Zoiko Rooms inventory only"],
                ),
                internal_results=internal_rows,
            )
            log_external_search_event(
                db,
                action="search_external.waterfall",
                resource_type="search",
                resource_id=correlation_id or "search",
                correlation_id=correlation_id,
                reason="internal_verified",
            )
            return result

        # Section 13 market activation gate: external discovery stays off
        # until Legal/Privacy/Commercial approve it and the flag is enabled.
        if not is_enabled(db, EXTERNAL_SEARCH_FLAG):
            log_external_search_event(
                db,
                action="search_external.not_activated",
                resource_type="search",
                resource_id=correlation_id or "search",
                correlation_id=correlation_id,
                reason="internal_zero;external_search_not_activated",
            )
            return OrchestratorResult(
                discovery=ExternalDiscoveryResult(
                    state=SearchState.INTERNAL_ZERO,
                    internal_matches=0,
                    external_matches=[],
                    fallback_triggered=False,
                    consent_required=False,
                    disclosure_text=NO_MATCH_DISCLOSURE,
                    guardrail_notes=["INTERNAL_ZERO", "EXTERNAL_SEARCH_NOT_ACTIVATED"],
                )
            )

        return self._external_fallback(db, query, correlation_id)

    def _search_internal(self, db: Session, query: SearchQuery) -> dict[str, Any]:
        """Section 5.1 qualifying-match gate over internal inventory.

        Qualifying only when:
        * publication state PUBLISHED (draft/paused/suspended/quarantined/
          withdrawn/archived and every other risk-hold state is excluded);
        * requested room type / objective amenities are satisfied;
        * budget + geography filters pass.
        Availability freshness is computed but only ever downgrades ranking
        (Section 5.2 priority 2), never hides a qualifying record.
        """
        from app.models.listing import Listing

        stmt = select(Listing).where(Listing.state == "PUBLISHED")
        if query.city:
            stmt = stmt.where(Listing.city.ilike(f"%{query.city}%"))
        if query.min_price is not None:
            stmt = stmt.where(Listing.price_per_night >= query.min_price)
        if query.max_price is not None:
            stmt = stmt.where(Listing.price_per_night <= query.max_price)
        rows = db.execute(stmt.limit(500)).scalars().all()
        verified_rooms = _verified_room_ids(db, {l.room_id for l in rows if l.room_id})

        q = (query.q or "").lower()
        objective_filters = [str(f).lower() for f in (query.objective_filters or [])]
        room_type = (query.room_type or "").lower()
        now = datetime.now(timezone.utc)
        freshness_limit = now - timedelta(days=settings.availability_freshness_days)

        out = []
        for l in rows:
            fields = " ".join(
                [l.name, l.city, l.location, l.description, l.room_type, l.property_type or ""]
            ).lower()
            if q and q not in fields:
                continue
            if room_type and room_type not in (l.room_type or "").lower():
                continue
            amenities = [str(a).lower() for a in (l.amenities or [])]
            if objective_filters and not all(f in amenities for f in objective_filters):
                continue

            confirmed_at = l.availability_confirmed_at
            is_stale = confirmed_at is None or confirmed_at <= freshness_limit
            out.append(
                {
                    "id": l.id,
                    "name": l.name,
                    "city": l.city,
                    "roomType": l.room_type,
                    "propertyType": l.property_type,
                    "state": l.state,
                    # Section 3.1 / SRCH-03: verification is reported as it
                    # is; publication (and any fee paid) never implies it.
                    "verificationStatus": "INTERNAL_VERIFIED"
                    if l.room_id in verified_rooms
                    else "INTERNAL_UNVERIFIED",
                    "pricePerMonth": l.price_per_night,
                    # The listing's own currency, so prices are never shown
                    # in the wrong one (e.g. INR listings as GBP).
                    "currency": l.currency,
                    "country": query.country,
                    "availabilityConfirmedAt": confirmed_at.isoformat() if confirmed_at else None,
                    "availabilityFreshStale": is_stale,
                    "rating": l.rating,
                    "reviewCount": l.review_count,
                    "amenities": list(l.amenities or []),
                    "publishedAt": l.published_at.isoformat() if l.published_at else None,
                    "_relevance": self._relevance(l, query),
                    "_user_fit": (sum(1 for f in objective_filters if f in amenities) / max(len(objective_filters), 1))
                    if objective_filters
                    else 0.0,
                    # Section 5.2 priority 3: verification status first,
                    # then quality signals.
                    "_trust": (10_000.0 if l.room_id in verified_rooms else 0.0)
                    + (float(l.rating or 0.0) * 10.0)
                    + min(int(l.review_count or 0), 999),
                    "_freshness_ts": (confirmed_at or l.published_at or now).timestamp()
                    if not is_stale
                    else 0.0,
                }
            )
        return {"rows": self._rank_internal(out)}

    @staticmethod
    def _relevance(listing: Any, query: SearchQuery) -> int:
        """Geographic + query relevance (Section 5.2 priority 1)."""
        city = (listing.city or "").lower()
        score = 0
        if query.city:
            qcity = query.city.lower()
            if city == qcity:
                score += 10
            elif qcity in city:
                score += 5
        if query.q:
            text = " ".join(
                [
                    listing.name or "",
                    listing.city or "",
                    listing.location or "",
                    listing.description or "",
                    listing.room_type or "",
                    listing.property_type or "",
                ]
            ).lower()
            score += sum(1 for t in query.q.lower().split() if t in text)
        return score

    @staticmethod
    def _rank_internal(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Section 5.2 internal ranking: relevance -> availability freshness ->
        trust/completeness -> user-preference fit -> stable determinism.

        Stale-unknown rows (freshness_ts 0) always sort below fresh ones; the
        final id tie-break keeps material ordering stable between searches.
        """
        return sorted(
            rows,
            key=lambda r: (
                -r["_relevance"],
                r["availabilityFreshStale"],
                -r["_freshness_ts"],
                -r["_trust"],
                -r["_user_fit"],
                str(r["id"]),
            ),
        )

    def confirm_availability(
        self,
        db: Session,
        listing_id: str,
        *,
        confirmed_by_party_id: int | None = None,
        correlation_id: str = "",
    ) -> Any:
        """Section 5.1 freshness: timestamp a host/party reconfirmation of a
        listing's availability. The stamp feeds the freshness ordering; calling
        this does not change publication state or imply 'confirmed available'
        in external language."""
        from app.models.listing import Listing

        listing = db.get(Listing, listing_id)
        if listing is None:
            raise ValueError("Listing not found")
        if listing.state != "PUBLISHED":
            raise PermissionError("Only published listings can confirm availability")
        listing.availability_confirmed_at = datetime.now(timezone.utc)
        log_external_search_event(
            db,
            action="availability.confirmed",
            resource_type="listing",
            resource_id=listing_id,
            correlation_id=correlation_id,
            reason=f"by_party={confirmed_by_party_id or ''}",
        )
        return listing

    def _external_fallback(
        self,
        db: Session,
        query: SearchQuery,
        correlation_id: str,
    ) -> OrchestratorResult:
        limit = query.limit_external or 10
        cards: list[ExternalCard] = []
        private: list[ExternalCandidate | ExternalOpportunity | None] = []
        registered_internally = 0

        for rule in registry.all_active():
            sid = str(rule.get("source_id"))
            if len(cards) >= limit or not registry.is_fallback_allowed(sid):
                continue
            if sid in WEB_SEARCH_SOURCE_IDS or rule.get("acquisition_mode") == "PUBLIC_FETCH":
                continue  # handled by the approved-website search below
            displayable = registry.is_displayable(sid)
            provider = PROVIDERS.get(sid)
            if provider is not None:
                candidates = self._fetch_provider(db, provider, rule, query, limit, correlation_id)
                if not displayable:
                    # Section 15.4: rights allow acquisition but not masked
                    # display -> keep as an internal opportunity only.
                    registered_internally += _register_internal_opportunities(db, candidates)
                    continue
                for cand in candidates[: limit - len(cards)]:
                    cards.append(_card_from_candidate(cand, rule, query))
                    private.append(
                        cand
                        if rule.get("contact_extraction_permitted")
                        else replace(cand, provider_contact=None)
                    )
            elif rule.get("acquisition_mode") == "PARTNER_FEED":
                # Tier A: listings already ingested from the partner's feed.
                if not displayable or not _in_territory(rule, query):
                    continue
                for opp in _partner_listings(db, sid, rule, query, limit - len(cards)):
                    cards.append(_card_from_candidate(_candidate_from_opportunity(opp), rule, query))
                    private.append(opp)
            elif displayable and not settings.is_production and not rule.get("acquisition_mode"):
                # Dev/test only: a seed-file source with no live adapter yields
                # one placeholder card so the masked-card flow can be exercised.
                cards.append(
                    ExternalCard.model_validate(
                        sanitizer.sanitize_card(
                            ExternalCard(
                                source_id=sid,
                                source_tier=str(rule.get("tier", "C")),
                                title=_SENTINEL_TITLE,
                                location_city=query.city,
                                location_country=query.country,
                            ).model_dump()
                        )
                    )
                )
                private.append(None)

        if len(cards) < limit:
            for cand, rule in self._search_approved_websites(db, query, limit - len(cards), correlation_id):
                if registry.is_displayable(cand.source_id):
                    cards.append(_card_from_candidate(cand, rule, query))
                    private.append(cand)
                else:
                    registered_internally += _register_internal_opportunities(db, [cand])

        if not cards:
            blocked = ExternalDiscoveryResult(
                state=SearchState.BLOCKED,
                internal_matches=0,
                external_matches=[],
                fallback_triggered=False,
                consent_required=False,
                disclosure_text=NO_MATCH_DISCLOSURE,
                guardrail_notes=["FALLBACK_NOT_ALLOWED: no rights-eligible external results"],
            )
            log_external_search_event(
                db,
                action="search_external.blocked",
                resource_type="search",
                resource_id=correlation_id or "search",
                correlation_id=correlation_id,
                reason=f"no_eligible_external_sources;registered_internally={registered_internally}",
            )
            return OrchestratorResult(discovery=blocked)

        raw_cards = [c.model_dump() for c in cards]

        discovered = ExternalDiscoveryResult(
            state=SearchState.EXTERNAL_DISCOVERED,
            internal_matches=0,
            external_matches=cards,
            fallback_triggered=True,
            consent_required=True,
            disclosure_text=EXTERNAL_DISCLOSURE,
            guardrail_notes=[
                "INTERNAL_ZERO",
                "EXTERNAL_FALLBACK_ELIGIBLE",
                "MASKED_CARDS_ONLY",
                "CONSENT_REQUIRED_BEFORE_CONTACT",
            ],
        )
        log_external_search_event(
            db,
            action="search_external.discovered",
            resource_type="search",
            resource_id=correlation_id or "search",
            correlation_id=correlation_id,
            reason=f"external_discovered:{len(cards)}",
        )
        return OrchestratorResult(
            discovery=discovered,
            external_raw=raw_cards,
            external_private=private,
        )

    @staticmethod
    def _search_approved_websites(
        db: Session, query: SearchQuery, limit: int, correlation_id: str
    ) -> list[tuple[ExternalCandidate, dict[str, Any]]]:
        """Last step of the waterfall: web search (Brave) restricted to
        websites that are themselves ACTIVE, approved PUBLIC_FETCH registry
        sources for this market. Only the search API is called; result pages
        are never fetched, and each hit takes its rights from its website's
        own registry row."""
        provider = web_search_provider()
        web_rule = registry.get(provider.source_id) if provider else None
        market = normalize_country(query.country)
        if (
            limit <= 0
            or provider is None
            or not query.city
            or not market
            or not web_rule
            or not registry.is_fallback_allowed(provider.source_id)
            or not _in_territory(web_rule, query)
        ):
            return []
        sites = {
            r["site_domain"]: r
            for r in registry.all_active()
            if r.get("acquisition_mode") == "PUBLIC_FETCH"
            and r.get("site_domain")
            and registry.is_fallback_allowed(str(r.get("source_id")))
            and _in_territory(r, query)
        }
        if not sites:
            return []
        try:
            broker.fetch_url_allowed(
                db, source_id=provider.source_id, url=provider.url, correlation_id=correlation_id
            )
        except BrokerAccessError:
            return []
        hits = fetch_web_hits(
            provider,
            city=query.city,
            market=market,
            domains=sorted(sites),
            limit=limit,
            ttl_seconds=int(web_rule.get("cache_ttl_seconds") or 0),
        )
        log_external_search_event(
            db,
            action="search_external.web_results",
            resource_type="external_fetch",
            resource_id=provider.source_id,
            correlation_id=correlation_id,
            reason=f"hits={len(hits)} sites={len(sites)}",
        )
        out = []
        for hit in hits:
            rule = sites[hit.domain]
            out.append(
                (
                    ExternalCandidate(
                        source_id=str(rule["source_id"]),
                        external_id=hashlib.sha256(hit.url.encode()).hexdigest()[:40],
                        city=query.city,
                        country=market,
                        source_url=hit.url,
                        room_type=hit.room_type,
                        advertised_price_minor=hit.price_minor,
                        currency=hit.currency,
                        price_period=hit.price_period,
                    ),
                    rule,
                )
            )
        return out

    @staticmethod
    def _fetch_provider(
        db: Session,
        provider: ListingProvider,
        rule: dict[str, Any],
        query: SearchQuery,
        limit: int,
        correlation_id: str,
    ) -> list[ExternalCandidate]:
        """Call a licensed listing API only for its own market, only with a
        city (Section 5.1: never silently widen geography) and only after the
        broker's rights + SSRF gate passes for the configured URL."""
        if not provider.configured() or not query.city:
            return []
        if normalize_country(query.country) not in provider.countries:
            return []
        if rule.get("territories") and not _in_territory(rule, query):
            return []
        try:
            broker.fetch_url_allowed(
                db, source_id=provider.source_id, url=provider.url, correlation_id=correlation_id
            )
        except BrokerAccessError:
            return []
        candidates = fetch_candidates(
            provider,
            city=query.city,
            limit=limit,
            ttl_seconds=int(rule.get("cache_ttl_seconds") or 0),
        )
        log_external_search_event(
            db,
            action="search_external.provider_results",
            resource_type="external_fetch",
            resource_id=provider.source_id,
            correlation_id=correlation_id,
            reason=f"candidates={len(candidates)}",
        )
        return candidates


def _verified_room_ids(db: Session, room_ids: set[int]) -> set[int]:
    """Rooms with BOTH a currently valid property verification and a valid
    listing-authority record (Section 11.1 existence + authority). One query
    per record type, never per listing."""
    if not room_ids:
        return set()
    from app.models.authority_record import AuthorityRecord
    from app.models.property_verification import PropertyVerification

    now = datetime.now(timezone.utc)

    def valid(model) -> set[int]:
        return set(
            db.scalars(
                select(model.room_id).where(
                    model.room_id.in_(room_ids),
                    model.status == "verified",
                    (model.expires_at.is_(None)) | (model.expires_at > now),
                )
            )
        )

    return valid(PropertyVerification) & valid(AuthorityRecord)


WEB_SEARCH_SOURCE_IDS = frozenset(p.source_id for p in WEB_SEARCH_PROVIDERS.values())


def _in_territory(rule: dict[str, Any], query: SearchQuery) -> bool:
    """A source serves only the markets in its registry territories; no
    territories or no recognisable market means no (fail closed)."""
    market = normalize_country(query.country)
    return bool(market) and market in set(rule.get("territories") or [])


def _partner_listings(
    db: Session, source_id: str, rule: dict[str, Any], query: SearchQuery, limit: int
) -> list[ExternalOpportunity]:
    """Fresh partner-feed listings in the requested city that fit the budget.
    Requires a city (Section 5.1: never silently widen geography) and drops
    anything older than the source's TTL (Section 6.3: never present stale
    data as current)."""
    if not query.city or limit <= 0:
        return []
    ttl = int(rule.get("cache_ttl_seconds") or 0) or 24 * 3600
    fresh_after = datetime.now(timezone.utc) - timedelta(seconds=ttl)
    rows = db.scalars(
        select(ExternalOpportunity)
        .where(
            ExternalOpportunity.source_id == source_id,
            ExternalOpportunity.discovered_by_user_id.is_(None),
            ExternalOpportunity.status.in_(("EXTERNAL_DISCOVERED", "OUTREACH_PENDING")),
            ExternalOpportunity.approx_location.ilike(f"%{_like_escape(query.city.strip())}%", escape="\\"),
            ExternalOpportunity.discovered_at >= fresh_after,
        )
        .order_by(ExternalOpportunity.discovered_at.desc(), ExternalOpportunity.id)
        .limit(200)
    ).all()
    out: list[ExternalOpportunity] = []
    for opp in rows:
        monthly = _candidate_from_opportunity(opp).rent_monthly
        if query.max_price is not None and monthly is not None and monthly > query.max_price:
            continue
        if query.min_price is not None and monthly is not None and monthly < query.min_price:
            continue
        out.append(opp)
        if len(out) >= limit:
            break
    return out


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _candidate_from_opportunity(opp: ExternalOpportunity) -> ExternalCandidate:
    return ExternalCandidate(
        source_id=opp.source_id,
        external_id=opp.external_opportunity_id,
        city=opp.approx_location,
        room_type=opp.room_type,
        advertised_price_minor=opp.advertised_price_minor,
        currency=opp.advertised_price_currency,
        price_period=opp.price_period,
    )


def _card_from_candidate(cand: ExternalCandidate, rule: dict[str, Any], query: SearchQuery) -> ExternalCard:
    """Masked consumer card with only the fields the source permits
    (Section 7.3). The title is never the source's own (source-title control)
    and location is the coarse area only."""
    permitted = set(rule.get("permitted_fields") or [])
    card = ExternalCard(
        source_id=cand.source_id,
        source_tier=str(rule.get("tier", "B")),
        title=_SENTINEL_TITLE,
        location_city=cand.city if "approx_location" in permitted else None,
        location_region=cand.region if "approx_location" in permitted else None,
        location_country=query.country,
        rent_monthly=cand.rent_monthly if "advertised_price" in permitted else None,
        currency=cand.currency if "advertised_price" in permitted and cand.rent_monthly else None,
        room_type=cand.room_type if "room_type" in permitted else None,
    )
    return ExternalCard.model_validate(sanitizer.sanitize_card(card.model_dump()))


def _register_internal_opportunities(db: Session, candidates: list[ExternalCandidate]) -> int:
    """Record non-displayable candidates as internal-only opportunities
    (no user, never shown). Already-known listings are skipped; the stale
    purge job removes them after the source TTL."""
    if not candidates:
        return 0
    known = set(
        db.scalars(
            select(ExternalOpportunity.dedupe_hash).where(
                ExternalOpportunity.dedupe_hash.in_([c.dedupe_hash for c in candidates])
            )
        )
    )
    added = 0
    for cand in candidates:
        if cand.dedupe_hash in known:
            continue
        db.add(_opportunity_from_candidate(cand, user_id=None, approx_location=cand.city))
        known.add(cand.dedupe_hash)
        added += 1
    db.flush()
    return added


def _opportunity_from_candidate(
    cand: ExternalCandidate, *, user_id: int | None, approx_location: str | None
) -> ExternalOpportunity:
    return ExternalOpportunity(
        external_opportunity_id=f"ext_{uuid4().hex[:12]}",
        source_id=cand.source_id,
        status="EXTERNAL_DISCOVERED",
        approx_location=approx_location,
        advertised_price_currency=cand.currency,
        advertised_price_minor=cand.advertised_price_minor,
        price_period=cand.price_period,
        room_type=cand.room_type,
        discovered_by_user_id=user_id,
        provider_name=cand.provider_name,
        # Section 12: raw source/contact data encrypted at rest.
        provider_contact_encrypted=encrypt_optional(cand.provider_contact),
        exact_address_encrypted=encrypt_optional(cand.exact_address),
        source_url_encrypted=encrypt_optional(cand.source_url),
        dedupe_hash=cand.dedupe_hash,
        verification_status="NOT_VERIFIED_BY_ZOIKO_ROOMS",
    )


def persist_external_cards(
    db: Session,
    cards: Iterable[ExternalCard],
    user_id: int,
    private: list[ExternalCandidate | ExternalOpportunity | None] | None = None,
) -> list[ExternalCardResult]:
    """Record each discovered card as an ExternalOpportunity owned by the
    searching user and return the consumer-safe card (Section 15.3).

    The REST route and the chat tool share this so both hand out a real
    opportunity id for the consent-gated contact flow, while the source
    identity (and any source record in ``private``) stays in the DB row and
    is excluded from the returned card.
    """
    out: list[ExternalCardResult] = []
    for i, card in enumerate(cards):
        approx = " ".join(
            part for part in (card.location_city or "", card.location_region or "") if part
        ).strip() or card.location_country or None
        cand = private[i] if private and i < len(private) else None
        if isinstance(cand, ExternalOpportunity):
            # Partner-feed listing: shared record, contactable by any renter.
            opp = cand
        elif cand is not None:
            opp = _opportunity_from_candidate(cand, user_id=user_id, approx_location=approx)
        else:
            opp = ExternalOpportunity(
                external_opportunity_id=f"ext_{uuid4().hex[:12]}",
                source_id=card.source_id,
                status="EXTERNAL_DISCOVERED",
                approx_location=approx,
                room_type=card.room_type or "PRIVATE_ROOM",
                discovered_by_user_id=user_id,
                verification_status="NOT_VERIFIED_BY_ZOIKO_ROOMS",
            )
        if opp.id is None:
            db.add(opp)
            db.flush()
        out.append(
            ExternalCardResult.model_validate(
                # Section 7.3: the card carries its discovery timestamp.
                {**card.model_dump(), "last_seen_at": opp.discovered_at, "opportunity_id": opp.id}
            )
        )
    return out


orchestrator = SearchOrchestrator()