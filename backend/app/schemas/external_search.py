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

    verification_status: Literal["unverified", "pending_consent", "consented", "blocked"] = Field(
        default="unverified"
    )
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


class ProviderContactRequest(BaseModel):
    """Chat-tool contact request. The source is resolved server-side from the
    opportunity, never supplied by the model."""

    model_config = ConfigDict(extra="ignore")

    opportunity_id: int = Field(..., ge=1)
    message: str = Field(..., min_length=1, max_length=2000)
    consent_fields: list[str] = Field(
        default_factory=lambda: ["desired_area", "move_in_window", "budget_band", "room_type"]
    )

    @field_validator("consent_fields")
    @classmethod
    def _lead_summary_only(cls, v: list[str]) -> list[str]:
        return sorted(set(v) & LEAD_SUMMARY_FIELDS) or ["desired_area"]


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
    # ge=1: a zero limit would hide internal matches and wrongly open the
    # external fallback (SRCH-01).
    limit_internal: int = Field(default=20, ge=1, le=100)
    limit_external: int = Field(default=10, ge=0, le=20)


class ExternalSearchRestResponse(BaseModel):
    """Safe search response -- internal rows plus masked external cards only."""

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