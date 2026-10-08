"""Market Legal Pack resolution (ZR-AI-SEARCH-001 Section 13).

"No market may enable PUBLIC_FETCH, automated provider outreach,
direct-contact release, referral/success fees or sensitive housing filters
until Legal/Privacy/Commercial have approved the corresponding Market Legal
Pack." Every helper here fails closed: no active, fully approved pack for the
market means the capability is off.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.external_search import ExternalMarketLegalPack
from app.services.external_providers import normalize_country


def active_pack(db: Session, market: str | None) -> ExternalMarketLegalPack | None:
    """Latest ACTIVE, Legal + Privacy + Commercial approved pack whose
    effective window covers now, or None."""
    code = normalize_country(market)
    if not code:
        return None
    now = datetime.now(timezone.utc)
    return db.scalar(
        select(ExternalMarketLegalPack)
        .where(
            ExternalMarketLegalPack.market_code == code,
            ExternalMarketLegalPack.status == "ACTIVE",
            ExternalMarketLegalPack.legal_approved.is_(True),
            ExternalMarketLegalPack.privacy_approved.is_(True),
            ExternalMarketLegalPack.commercial_approved.is_(True),
            ExternalMarketLegalPack.effective_from <= now,
            or_(ExternalMarketLegalPack.effective_to.is_(None), ExternalMarketLegalPack.effective_to > now),
        )
        .order_by(ExternalMarketLegalPack.version.desc())
        .limit(1)
    )


def external_search_allowed(db: Session, market: str | None, *, public_visitor: bool = False) -> bool:
    pack = active_pack(db, market)
    if pack is None or not pack.external_search_enabled:
        return False
    return pack.public_visitor_search_enabled if public_visitor else True


def public_fetch_allowed(db: Session, market: str | None) -> bool:
    pack = active_pack(db, market)
    return bool(pack and pack.external_search_enabled and pack.public_fetch_enabled)


def outreach_channel_allowed(db: Session, market: str | None, channel: str | None) -> bool:
    pack = active_pack(db, market)
    return bool(
        pack
        and pack.provider_outreach_enabled
        and channel
        and channel in (pack.permitted_outreach_channels or [])
    )


def contact_release_allowed(db: Session, market: str | None) -> bool:
    pack = active_pack(db, market)
    return bool(pack and pack.direct_contact_release_enabled)


def referral_fees_allowed(db: Session, market: str | None) -> bool:
    pack = active_pack(db, market)
    return bool(pack and pack.referral_fees_enabled)


def prohibited_terms(db: Session, market: str | None) -> list[str]:
    """Extra market-specific prohibited search/filter vocabulary (lower-case)."""
    pack = active_pack(db, market)
    return [str(t).strip().lower() for t in (pack.prohibited_search_terms if pack else []) if str(t).strip()]
