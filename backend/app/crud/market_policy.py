from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.audit import log_audit_event
from app.models.admin_user import AdminUser
from app.models.market_policy import MARKET_POLICY_CONFIDENCE_LEVELS, MarketPolicyPack

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


def list_market_policy_packs(db: Session, jurisdiction_code: str | None = None) -> list[MarketPolicyPack]:
    query = select(MarketPolicyPack).order_by(MarketPolicyPack.jurisdiction_code, MarketPolicyPack.version.desc())
    if jurisdiction_code:
        query = query.where(MarketPolicyPack.jurisdiction_code == jurisdiction_code)
    return list(db.scalars(query))


def get_market_policy_pack_or_404(db: Session, pack_id: int) -> MarketPolicyPack:
    pack = db.get(MarketPolicyPack, pack_id)
    if not pack:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Market policy pack not found")
    return pack


def create_market_policy_pack(db: Session, admin: AdminUser, data: dict) -> MarketPolicyPack:
    """Admin surface for what was previously only creatable in test fixtures
    -- every jurisdiction pack this platform actually runs on (including
    England's) needs a real, audited way to exist. Always creates the next
    version for this jurisdiction_code (append-only-by-version, same rule
    resolve_market_policy already assumes); never edits a prior version's
    already-applied rules in place."""
    confidence = data.get("confidence", "REVIEW_REQUIRED")
    if confidence not in MARKET_POLICY_CONFIDENCE_LEVELS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"confidence must be one of {list(MARKET_POLICY_CONFIDENCE_LEVELS)}")

    jurisdiction_code = data["jurisdiction_code"]
    if len(jurisdiction_code) > 10:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "jurisdiction_code must be 10 characters or fewer")
    latest = db.scalar(
        select(MarketPolicyPack)
        .where(MarketPolicyPack.jurisdiction_code == jurisdiction_code)
        .order_by(MarketPolicyPack.version.desc())
    )
    next_version = (latest.version + 1) if latest else 1

    pack = MarketPolicyPack(**{**data, "version": next_version})
    db.add(pack)
    db.commit()
    db.refresh(pack)

    log_audit_event(
        db, admin, "market_policy_pack.create", "market_policy_pack", str(pack.id),
        reason=f"jurisdiction={jurisdiction_code}; version={next_version}; confidence={confidence}",
    )
    return pack


def update_market_policy_pack(db: Session, admin: AdminUser, pack: MarketPolicyPack, updates: dict) -> MarketPolicyPack:
    """In-place edit of an existing pack row -- for correcting a mistake or
    extending effective_to, not for changing rules already relied upon by
    past decisions (those get a new version via create_market_policy_pack
    instead, so policy snapshots taken at decision time stay meaningful)."""
    if "confidence" in updates and updates["confidence"] is not None and updates["confidence"] not in MARKET_POLICY_CONFIDENCE_LEVELS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"confidence must be one of {list(MARKET_POLICY_CONFIDENCE_LEVELS)}")

    for field, value in updates.items():
        if value is not None:
            setattr(pack, field, value)
    db.commit()
    db.refresh(pack)

    log_audit_event(
        db, admin, "market_policy_pack.update", "market_policy_pack", str(pack.id),
        reason=f"jurisdiction={pack.jurisdiction_code}; version={pack.version}",
    )
    return pack
