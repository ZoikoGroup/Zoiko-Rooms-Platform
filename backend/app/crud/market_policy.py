from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market_policy import MarketPolicyPack
from app.models.occupancy import Occupancy

DEFAULT_JURISDICTION = "IN"


def jurisdiction_code_for_occupancy(occupancy: Occupancy) -> str:
    """ZR-ENG-CLR-006 Section 6: 'The Termination Policy Resolver must
    select an effective-dated market rule set using the property
    jurisdiction.' The one call site this build resolves a *real* property
    jurisdiction from, rather than the platform-wide DEFAULT_JURISDICTION
    -- see crud/termination.py's own call sites."""
    return occupancy.room.property.jurisdiction_code


def resolve_market_policy(db: Session, jurisdiction_code: str = DEFAULT_JURISDICTION, *, as_of: date | None = None) -> MarketPolicyPack:
    """The single entry point deposit/sublet code must use to get jurisdiction
    rules -- never branch on jurisdiction_code directly (ZR-ENG-CLR-002 Section
    3.2 / ZR-ENG-CLR-003 Section 13.1's explicit 'no hard-coded country
    branches' rule). Picks the highest-version pack whose effective window
    covers as_of (defaults to today)."""
    as_of = as_of or date.today()
    policy = db.scalar(
        select(MarketPolicyPack)
        .where(
            MarketPolicyPack.jurisdiction_code == jurisdiction_code,
            MarketPolicyPack.effective_from <= as_of,
            (MarketPolicyPack.effective_to.is_(None)) | (MarketPolicyPack.effective_to >= as_of),
        )
        .order_by(MarketPolicyPack.version.desc())
    )
    if not policy:
        # ZR-ENG-CLR-003's own fail-safe rule: if the market pack can't be
        # resolved, never silently fall back to some default behavior.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"No market policy pack configured for jurisdiction '{jurisdiction_code}' as of {as_of} -- REVIEW_REQUIRED",
        )
    return policy


def to_policy_snapshot(policy: MarketPolicyPack) -> dict:
    """The dict persisted onto deposit_instruments.calculation_snapshot /
    sublet_requests.policy_snapshot -- an immutable record of which rule
    version applied to a specific transaction, not a live reference."""
    return {
        "jurisdiction": policy.jurisdiction_code,
        "policy_pack_id": policy.id,
        "policy_pack_version": policy.version,
        "confidence": policy.confidence,
    }


def to_termination_policy_snapshot(policy: MarketPolicyPack) -> dict:
    """ZR-ENG-CLR-006 Section 6/19's termination_policy_snapshot -- the same
    'immutable per calculation' discipline as to_policy_snapshot above, widened
    to also freeze every field the Termination/Refund engine actually reads
    (notice, liability model, fee) rather than just the four bookkeeping keys.
    A later refund_entitlement calculation always reads case.policy_snapshot,
    never re-resolves the (possibly since-changed) live MarketPolicyPack row
    -- AC-02's own 'reproducible from the policy_snapshot_id, not from
    whatever policy happens to be current' rule, now covering every dimension
    this engine actually uses instead of only notice_days."""
    snapshot = to_policy_snapshot(policy)
    snapshot.update({
        "termination_notice_days": policy.termination_notice_days,
        "align_termination_to_rent_cycle": policy.align_termination_to_rent_cycle,
        "termination_liability_model": policy.termination_liability_model,
        "termination_break_fee_rent_multiple": float(policy.termination_break_fee_rent_multiple),
        "termination_liability_cap_rent_multiple": (
            float(policy.termination_liability_cap_rent_multiple)
            if policy.termination_liability_cap_rent_multiple is not None else None
        ),
    })
    return snapshot
