from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market_policy import MarketPolicyPack

DEFAULT_JURISDICTION = "IN"


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
