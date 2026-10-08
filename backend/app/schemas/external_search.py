"""Pydantic schemas for the ZR-AI-SEARCH-001 external search protocol.

Canonical states, masked external cards, and provider-outreach payloads.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SearchState(str, Enum):
    INTERNAL_VERIFIED = "INTERNAL_VERIFIED"
    INTERNAL_ZERO = "INTERNAL_ZERO"
    EXTERNAL_FALLBACK_ELIGIBLE = "EXTERNAL_FALLBACK_ELIGIBLE"
    EXTERNAL_QUEUED = "EXTERNAL_QUEUED"
    EXTERNAL_DISCOVERED = "EXTERNAL_DISCOVERED"
    NEEDS_PROVIDER_CONSENT = "NEEDS_PROVIDER_CONSENT"
    REQUIRES_UNLOCK = "REQUIRES_UNLOCK"
    BLOCKED = "BLOCKED"


class ExternalSourceTier(str, Enum):
    TIER_A = "A"
    TIER_B = "B"
    TIER_C = "C"
    BLOCKED = "BLOCKED"


class ExternalCard(BaseModel):
    """Safe external card schema -- no URLs, phones, emails, or exact addresses
    before unlock (ZR-AI-SEARCH-001 safe-card rules)."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(..., min_length=1, max_length=64)
    source_tier: str = Field(...)
    canonical_id: str | None = Field(default=None, max_length=128)
    title: str = Field(..., min_length=1, max_length=200)
    location_city: str | None = Field(default=None, max_length=120)
    location_region: str | None = Field(default=None, max_length=120)
    location_country: str | None = Field(default=None, max_length=80)
    rent_monthly: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    deposit: int | None = Field(default=None, ge=0)
    availability_text: str | None = Field(default=None, max_length=120)
    room_type: str | None = Field(default=None, max_length=60)
    occupancy: str | None = Field(default=None, max_length=60)
    amenities: list[str] = Field(default_factory=list)
    distance_km: float | None = Field(default=None, ge=0)
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    images_present: bool = Field(default=False)
    last_seen_at: datetime | None = Field(default=None)

    has_exact_address: bool = Field(default=False)
    has_phone: bool = Field(default=False)
    has_email: bool = Field(default=False)
    has_url: bool = Field(default=False)

    # Section 15.3: the exact label every external card carries.
    verification_status: Literal[
        "NOT_VERIFIED_BY_ZOIKO_ROOMS", "unverified", "pending_consent", "consented", "blocked"
    ] = Field(default="NOT_VERIFIED_BY_ZOIKO_ROOMS")
    is_unlocked: bool = Field(default=False)


class ExternalCardResult(ExternalCard):
    """External card as returned by the REST search endpoint -- the safe card
    fields plus a persisted opportunity id so the client can start a
    consent-gated contact request. No contact details are added."""

    model_config = ConfigDict(extra="ignore")

    # Section 8 / SRCH-05: the source identity stays server-side; it is
    # accepted from the internal card but never serialized to clients or the LLM.
    source_id: str | None = Field(default=None, exclude=True)
    source_tier: str | None = Field(default=None, exclude=True)

    opportunity_id: int | None = Field(default=None, ge=1)

    # Section 15.3 safe-schema fields (alongside the fields above).
    external_opportunity_id: str | None = Field(default=None, max_length=100)
    status: Literal["EXTERNAL_DISCOVERED"] = "EXTERNAL_DISCOVERED"
    approx_location: str | None = Field(default=None, max_length=300)
    advertised_price: dict[str, Any] | None = None
    discovered_at: datetime | None = None
    primary_cta: Literal["REQUEST_ZOIKO_CONTACT"] = "REQUEST_ZOIKO_CONTACT"


class ExternalDiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: SearchState = Field(...)
    internal_matches: int = Field(default=0, ge=0)
    external_matches: list[ExternalCard] = Field(default_factory=list)
    fallback_triggered: bool = Field(default=False)
    consent_required: bool = Field(default=False)
    disclosure_text: str = Field(
        default="External results are not verified by Zoiko Rooms."
    )
    guardrail_notes: list[str] = Field(default_factory=list)
    audit_id: str | None = Field(default=None)
    # Section 15.2 / 15.3 envelope.
    query_id: str | None = Field(default=None)
    search_route: Literal["INTERNAL_ONLY", "EXTERNAL_FALLBACK", "NONE"] | None = None
    external_search_status: str | None = None


class ProviderContactRequest(BaseModel):
    """Chat-tool contact request. The source is resolved server-side from the
    opportunity, never supplied by the model."""

    model_config = ConfigDict(extra="ignore")

    opportunity_id: int = Field(..., ge=1)
    message: str = Field(..., min_length=1, max_length=2000)
    consent_fields: list[str] = Field(
        default_factory=lambda: ["desired_area", "move_in_window", "budget_band", "room_type"]
    )
    # Section 7.4: the user must have confirmed what will be shared.
    user_confirmed_sharing: bool = False
    lead_details: dict[str, str] = Field(default_factory=dict)

    @field_validator("consent_fields")
    @classmethod
    def _lead_summary_only(cls, v: list[str]) -> list[str]:
        return sorted(set(v) & LEAD_SUMMARY_FIELDS) or ["desired_area"]

    @field_validator("lead_details")
    @classmethod
    def _short_lead_details(cls, v: dict[str, str]) -> dict[str, str]:
        return {k: " ".join(str(val).split())[:120] for k, val in v.items() if k in LEAD_SUMMARY_FIELDS and str(val).strip()}


class ProviderOutreachCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opportunity_id: int = Field(..., ge=1)
    requested_by_user_id: int = Field(..., ge=1)
    channel: Literal["EMAIL", "SMS", "PLATFORM_MESSAGE", "TELEPHONE"] = "PLATFORM_MESSAGE"
    consent_record: dict[str, Any] = Field(default_factory=dict)
    audit_trail: list[dict[str, Any]] = Field(default_factory=list)


class SourceRightEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    domain: str | None = None
    tier: str
    allow_fallback: bool = True
    allow_direct_contact: bool = False
    allow_indexing: bool = False
    policy_ref: str | None = None
    notes: str | None = None
    is_active: bool = True


class ExternalSearchRestRequest(BaseModel):
    """REST shape of the ZR-AI-SEARCH-001 search request (Section 15.1).

    Supports the qualifying-match filters (Section 5.1): geography, budget,
    move-in window, room type and objective amenities.
    """

    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=80)
    min_price: int | None = Field(default=None, ge=0)
    max_price: int | None = Field(default=None, ge=0)
    move_in_from: date | None = None
    move_in_to: date | None = None
    room_type: str | None = Field(default=None, max_length=60)
    objective_filters: list[str] = Field(default_factory=list, max_length=50)
    # Section 15.1: market and an optional search radius around a point.
    market_code: str | None = Field(default=None, min_length=2, max_length=2)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_m: int | None = Field(default=None, ge=100, le=100_000)
    # ge=1: a zero limit would hide internal matches and wrongly open the
    # external fallback (SRCH-01).
    limit_internal: int = Field(default=20, ge=1, le=100)
    limit_external: int = Field(default=10, ge=0, le=20)


class ExternalSearchRestResponse(BaseModel):
    """Safe search response -- internal rows plus masked external cards only."""

    query_id: str | None = None
    search_route: str | None = None
    external_search_status: str | None = None
    state: str
    internal_matches: int = 0
    internal_results: list[dict[str, Any]] = Field(default_factory=list)
    external_matches: list[ExternalCardResult] = Field(default_factory=list)
    fallback_triggered: bool = False
    consent_required: bool = False
    disclosure_text: str = Field(default="External results are not verified by Zoiko Rooms.")
    guardrail_notes: list[str] = Field(default_factory=list)
    audit_id: str | None = None


# Section 7.4 "may share initially": the minimal lead summary. Name, phone,
# email, ID, exact address and payment data are withheld until the provider
# accepts, so they are not valid consent fields at request time.
LEAD_SUMMARY_FIELDS = frozenset(
    {"desired_area", "move_in_window", "budget_band", "room_type", "occupants", "requirements"}
)


class ExternalContactRestRequest(BaseModel):
    """User's request for Zoiko Rooms to contact an external provider."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., min_length=1, max_length=2000)
    consent_fields: list[str] = Field(..., min_length=1, max_length=len(LEAD_SUMMARY_FIELDS))
    # Optional values for consented lead-summary fields (e.g. move_in_window
    # "from mid-November", budget_band "GBP 800-1000"). Only keys the renter
    # consented to share are kept.
    lead_details: dict[str, str] = Field(default_factory=dict)

    @field_validator("lead_details")
    @classmethod
    def _short_lead_details(cls, v: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, value in v.items():
            if key not in LEAD_SUMMARY_FIELDS:
                raise ValueError(f"unknown lead detail: {key}")
            text = " ".join(str(value).split())[:120]
            if text:
                out[key] = text
        return out

    @field_validator("consent_fields")
    @classmethod
    def _lead_summary_only(cls, v: list[str]) -> list[str]:
        not_allowed = sorted(set(v) - LEAD_SUMMARY_FIELDS)
        if not_allowed:
            raise ValueError(f"not shareable before provider acceptance: {', '.join(not_allowed)}")
        return sorted(set(v))


class OutreachCreated(BaseModel):
    model_config = ConfigDict(extra="ignore")

    outreach_id: int
    status: str
    channel: str

class SourceRegistryUpsert(BaseModel):
    """Super-admin create/update of one Source Rights Registry row (Section
    6.2). Approvals are explicit; every flag defaults to closed."""

    model_config = ConfigDict(extra="forbid")

    source_name_internal: str = Field(..., min_length=1, max_length=200)
    acquisition_mode: Literal["PARTNER_FEED", "LICENSED_API", "PUBLIC_FETCH", "BLOCKED"]
    status: Literal["ACTIVE", "REVIEW", "SUSPENDED", "BLOCKED"] = "REVIEW"
    territories: list[Literal["GB", "US", "AU", "IN"]] = Field(default_factory=list)
    terms_reference: str | None = Field(default=None, max_length=500)
    legal_approved: bool = False
    security_approved: bool = False
    permitted_fields: list[
        Literal[
            "approx_location", "advertised_price", "room_type", "price_minor", "currency",
            "price_period", "provider_name", "provider_contact", "exact_address", "source_url",
        ]
    ] = Field(default_factory=list)
    display_permitted: bool = False
    masking_permitted: bool = False
    attribution_required: bool = False
    clickthrough_required: bool = False
    contact_extraction_permitted: bool = False
    outreach_permitted: bool = False
    outreach_channels: list[Literal["EMAIL", "SMS", "PLATFORM_MESSAGE", "TELEPHONE"]] = Field(default_factory=list)
    cache_ttl_seconds: int = Field(default=3600, ge=60, le=7 * 24 * 3600)
    feed_url: str | None = Field(default=None, max_length=1000)
    feed_format: Literal["JSON", "CSV", "BLM", "RESO"] | None = None
    feed_credential_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{2,99}$")
    site_domain: str | None = Field(
        default=None,
        max_length=253,
        # Lowercase hostname, e.g. "lettings.example.co.uk"; labels don't start or end with "-".
        pattern=r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$",
    )
