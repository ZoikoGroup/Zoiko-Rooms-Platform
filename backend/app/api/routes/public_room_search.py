"""Anonymous room search for the Zoiko Rooms marketing website
(ZR-AI-SEARCH-001, same waterfall as the signed-in assistant).

Zoiko Rooms listings first; only when there are none, approved external
sources as masked "Not verified by Zoiko Rooms" cards. Differences from the
signed-in search, all intentional:

* No login. Nothing is stored for the visitor: external cards carry no
  opportunity id, and asking Zoiko Rooms to contact a provider requires
  signing in on the platform (consent and ownership need an account).
* External results need their own flag (external.public_search_fallback) on
  top of external.search_fallback, so anonymous traffic cannot spend external
  search credits unless that is switched on.
* Rate limited per visitor with the shared Postgres-backed budget. The
  website's server calls this endpoint server-to-server, so it authenticates
  with PUBLIC_SEARCH_SERVICE_TOKEN and passes a hashed visitor id in
  X-Visitor-Id; any other caller is limited by its own IP.
* Only a fixed set of listing fields is returned (no ranking internals).
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.correlation import get_correlation_id
from app.db.session import get_db
from app.schemas.external_search import ExternalCardResult
from app.services.feature_flags import is_enabled
from app.services.public_rate_limit import check_public_rate_limit
from app.services.search_orchestrator import SearchQuery, orchestrator, section_15_fields

router = APIRouter(prefix="/api/public/rooms", tags=["public-room-search"])

PUBLIC_EXTERNAL_FLAG = "external.public_search_fallback"
SIGN_IN_TO_CONTACT = (
    "To ask Zoiko Rooms to contact a provider, sign in to your Zoiko Rooms account."
)
# Listing fields a visitor may see; everything else (ranking signals,
# internal timestamps) stays server-side.
PUBLIC_LISTING_FIELDS = (
    "id", "slug", "name", "city", "roomType", "propertyType", "pricePerMonth",
    "currency", "verificationStatus", "amenities", "rating", "reviewCount",
    "availabilityConfirmedAt",
)
_VISITOR_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class PublicRoomSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str = Field(..., min_length=1, max_length=120)
    country: str = Field(..., min_length=1, max_length=80)
    q: str | None = Field(default=None, max_length=200)
    min_price: int | None = Field(default=None, ge=0)
    max_price: int | None = Field(default=None, ge=0)
    room_type: str | None = Field(default=None, max_length=60)


class PublicRoomSearchResponse(BaseModel):
    query_id: str | None = None
    search_route: str | None = None
    external_search_status: str | None = None
    state: str
    internal_matches: int
    internal_results: list[dict]
    external_matches: list[ExternalCardResult]
    disclosure_text: str
    contact_note: str | None = None


def _principal(request: Request, service_token: str | None, visitor_id: str | None) -> str:
    """Rate-limit identity. A trusted website server (valid service token)
    names the visitor; anyone else is bucketed by their own IP, and a visitor
    id without a valid token is ignored so it cannot be used to dodge limits."""
    expected = settings.public_search_service_token
    trusted = bool(expected) and bool(service_token) and hmac.compare_digest(service_token, expected)
    if trusted and visitor_id and _VISITOR_ID.match(visitor_id):
        return f"rooms:visitor:{visitor_id}"
    # Deployed behind a reverse proxy, the direct peer is the proxy, so use the
    # first X-Forwarded-For hop when present (same rule as public_assistant).
    forwarded = request.headers.get("x-forwarded-for")
    ip = (forwarded.split(",")[0].strip() if forwarded else "") or (
        request.client.host if request.client else "unknown"
    )
    return "rooms:ip:" + hashlib.sha256(ip.encode()).hexdigest()[:32]


@router.post("/search", response_model=PublicRoomSearchResponse)
def public_room_search(
    payload: PublicRoomSearchRequest,
    request: Request,
    db: Session = Depends(get_db),
    x_service_token: str | None = Header(default=None),
    x_visitor_id: str | None = Header(default=None),
) -> PublicRoomSearchResponse:
    allowed = check_public_rate_limit(
        db,
        principal=_principal(request, x_service_token, x_visitor_id),
        limit=settings.public_room_search_rate_limit_max,
        window_seconds=settings.public_room_search_rate_limit_window_seconds,
    )
    db.commit()  # persist the shared counter
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many searches. Please wait a moment and try again.",
        )

    query = SearchQuery(
        q=payload.q,
        city=payload.city.strip(),
        country=payload.country.strip(),
        min_price=payload.min_price,
        max_price=payload.max_price,
        room_type=payload.room_type,
        limit_internal=20,
        limit_external=10,
    )
    result = orchestrator.search(
        db,
        query,
        correlation_id=get_correlation_id(request),
        allow_external=is_enabled(db, PUBLIC_EXTERNAL_FLAG),
        public_visitor=True,
    )
    # The orchestrator's audit rows and nothing else are kept for visitors.
    db.commit()

    disc = result.discovery
    # Visitor cards are never stored, so stamp the discovery time here
    # (Section 7.3: "discovered on [date/time]").
    discovered_at = datetime.now(timezone.utc)
    external = [
        ExternalCardResult.model_validate(
            {
                **card.model_dump(),
                "last_seen_at": card.last_seen_at or discovered_at,
                **section_15_fields(card, None, card.last_seen_at or discovered_at),
            }
        )
        for card in disc.external_matches
    ]
    return PublicRoomSearchResponse(
        query_id=disc.query_id,
        search_route=disc.search_route,
        external_search_status=disc.external_search_status,
        state=disc.state.value,
        internal_matches=disc.internal_matches,
        internal_results=[
            {k: row.get(k) for k in PUBLIC_LISTING_FIELDS} for row in result.internal_results
        ],
        external_matches=external,
        disclosure_text=disc.disclosure_text,
        contact_note=SIGN_IN_TO_CONTACT if external else None,
    )
