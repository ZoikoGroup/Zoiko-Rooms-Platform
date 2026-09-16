"""ZR-ENG-CLR-012 Section 7: the Requirement Resolver -- "the only component
permitted to determine which verification requirements apply." Deliberately
thin: this MVP resolves exactly one non-universal requirement
(OCCUPANCY_ELIGIBILITY) from the same versioned, effective-dated
MarketPolicyPack every other jurisdiction-aware domain in this codebase
already uses (Sections 2/3/8's deposit/sublet/rent-change policy) --
booking/agreement code must call this, never branch on jurisdiction_code
directly (the doc's own "no hard-coded country branches" rule, restated for
verification).

IDENTITY is not resolved here: it is already unconditionally required
platform-wide at application submission (crud/identity_verification.py),
so there is no jurisdiction decision to make for it in this MVP. A future
jurisdiction that wanted to relax that (Section 5's "Application-stage
verification should be progressive") would extend this resolver, not add
a second one."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud.market_policy import resolve_market_policy
from app.models.market_policy import MarketPolicyPack


@dataclass(frozen=True)
class ResolvedRequirement:
    requirement_code: str
    method_note: str
    follow_up_days: int | None
    policy_pack_version: int


def _resolve_with_subnational_fallback(db: Session, jurisdiction_code: str) -> MarketPolicyPack | None:
    """ZR-ENG-CLR-012 AC-16: 'The Requirement Resolver is effective-dated and
    supports country plus subnational/local rules.' Convention: a hyphenated
    code (e.g. 'GB-SCT') names a subnational unit of the country before the
    hyphen ('GB'). If no pack is configured for the exact subnational code,
    fall back to the country-level pack rather than treating it as fully
    unconfigured -- scoped to this module only (never resolve_market_policy
    itself, which deposit/sublet/rent-change also call and must keep its own
    exact-match fail-closed behavior unchanged)."""
    try:
        return resolve_market_policy(db, jurisdiction_code)
    except HTTPException:
        pass
    if "-" in jurisdiction_code:
        parent_code = jurisdiction_code.split("-", 1)[0]
        try:
            return resolve_market_policy(db, parent_code)
        except HTTPException:
            return None
    return None


def resolve_verification_requirements(db: Session, jurisdiction_code: str) -> list[ResolvedRequirement]:
    """Returns the list of ADDITIONAL (beyond baseline identity) verification
    requirements this jurisdiction imposes. Unlike deposit/sublet/rent-change
    policy (mandatory for every money-moving jurisdiction, so a missing pack
    correctly fails closed), occupancy eligibility is opt-in-per-jurisdiction
    by the doc's own design ("no jurisdiction requires this by default; only
    named ones impose it") -- so a jurisdiction with no policy pack
    configured yet safely resolves to "nothing additional applies", not to
    blocking every agreement in that jurisdiction."""
    policy = _resolve_with_subnational_fallback(db, jurisdiction_code)
    if policy is None:
        return []

    requirements: list[ResolvedRequirement] = []
    if policy.occupancy_eligibility_required:
        requirements.append(ResolvedRequirement(
            requirement_code="OCCUPANCY_ELIGIBILITY",
            method_note=policy.occupancy_eligibility_method_note,
            follow_up_days=policy.occupancy_eligibility_follow_up_days,
            policy_pack_version=policy.version,
        ))
    return requirements


def resolve_property_compliance_requirements(db: Session, jurisdiction_code: str) -> list[str]:
    """ZR-ENG-CLR-012 Section 14: which property-compliance credential codes
    (gas safety, EPC, HMO license, etc.) are mandatory before a listing in
    this jurisdiction can publish. Deliberately returns opaque codes, not a
    document list or their meaning -- that mapping is real per-jurisdiction
    law owned by MarketPolicyPack.required_property_compliance_codes, never
    hard-coded here. Empty list (including "no policy pack for this
    jurisdiction yet") means no property-compliance gate applies, same
    fail-open posture as OCCUPANCY_ELIGIBILITY above."""
    policy = _resolve_with_subnational_fallback(db, jurisdiction_code)
    if policy is None:
        return []
    return list(policy.required_property_compliance_codes or [])


def is_identity_required_at_application(db: Session, jurisdiction_code: str) -> bool:
    """ZR-ENG-CLR-012 AC-03: 'Application can proceed before full ID
    completion unless the active market pack explicitly requires an
    earlier gate.' False (not required) whenever no pack is configured for
    this jurisdiction -- same fail-open posture as every other resolver
    here; a jurisdiction only gets this gate by explicitly opting in."""
    policy = _resolve_with_subnational_fallback(db, jurisdiction_code)
    if policy is None:
        return False
    return policy.identity_required_at_application


def is_screening_check_type_permitted(db: Session, jurisdiction_code: str, check_type: str) -> bool:
    """ZR-ENG-CLR-012 Sections 10/11/AC-17: 'Prohibited document types in a
    jurisdiction cannot be requested through UI, API or Host free text',
    applied to screening/affordability check types. No policy pack for the
    jurisdiction yet means nothing has been prohibited there -- permitted
    by default, same fail-open posture as every other resolver here (this
    is a permission list, not a mandatory-requirement list, so fail-open
    means 'not yet restricted', not 'not yet decided to check')."""
    policy = _resolve_with_subnational_fallback(db, jurisdiction_code)
    if policy is None:
        return True
    return check_type not in (policy.screening_prohibited_check_types or [])
