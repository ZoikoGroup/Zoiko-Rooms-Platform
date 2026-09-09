"""ZR-ENG-CLR-004 Section 5.3: the Host template completion states.

'The Host should not be asked to "write a lease." The Host supplies factual
information and selects only legally approved options.' In this codebase
almost every fact the spec's Host Input Template collects (landlord
identity, premises, rent, deposit) is already known from verified
listing/offer/party data by the time an offer is accepted -- there is no
separate Host-facing drafting session. What this module actually computes is
the *readiness* state: given what's already on file, is a safe agreement
profile resolvable, are the pipeline's own eligibility gates satisfied, and
has generation/execution progressed. It deliberately reuses
resolve_agreement_profile and check_agreement_eligibility rather than
re-deriving 'what's missing' -- those two functions are what actually gate
create_agreement, so this can never report READY_TO_GENERATE when
create_agreement would in fact reject the request, or vice versa.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.crud.eligibility import check_agreement_eligibility
from app.models.leasing import Offer
from app.services.agreement_profile import resolve_agreement_profile

HOST_READINESS_STATES = (
    "NOT_STARTED",
    "BLOCKED_POLICY",
    "NEEDS_VERIFICATION",
    "READY_TO_GENERATE",
    "GENERATED_FOR_REVIEW",
    "LOCKED_FOR_EXECUTION",
)


@dataclass(frozen=True)
class HostReadiness:
    state: str
    missing_facts: list[str] = field(default_factory=list)


def compute_host_readiness(db: Session, offer: Offer) -> HostReadiness:
    agreement = offer.agreement
    if agreement is not None:
        latest_version = agreement.versions[-1] if agreement.versions else None
        if latest_version is not None and latest_version.status == "EXECUTED_IMMUTABLE":
            return HostReadiness(state="LOCKED_FOR_EXECUTION")
        return HostReadiness(state="GENERATED_FOR_REVIEW")

    if not offer.terms:
        return HostReadiness(state="NOT_STARTED")

    profile = resolve_agreement_profile(db, offer.listing, offer.listing.room)
    if profile is None:
        return HostReadiness(state="BLOCKED_POLICY")

    reasons = check_agreement_eligibility(db, offer)
    if reasons:
        return HostReadiness(state="NEEDS_VERIFICATION", missing_facts=reasons)

    return HostReadiness(state="READY_TO_GENERATE")
