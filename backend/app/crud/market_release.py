from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin_user import AdminUser
from app.models.market_release import MarketRelease
from app.schemas.marketplace import MarketReleaseCreate
from app.services.policy import set_policy_overrides


def list_market_releases(db: Session) -> list[MarketRelease]:
    return list(db.scalars(select(MarketRelease).order_by(MarketRelease.id)))


def get_market_release(db: Session, market_release_id: int) -> MarketRelease | None:
    return db.get(MarketRelease, market_release_id)


def create_market_release(db: Session, data: MarketReleaseCreate) -> MarketRelease:
    release = MarketRelease(jurisdiction=data.jurisdiction, min_stay_nights=data.min_stay_nights)
    db.add(release)
    db.commit()
    db.refresh(release)
    return release


def set_market_release_status(db: Session, release: MarketRelease, status: str, approver: AdminUser) -> MarketRelease:
    release.status = status
    if status == "active":
        release.approved_by_admin_id = approver.id
        release.approved_at = datetime.now(timezone.utc)
        if release.effective_from is None:
            release.effective_from = datetime.now(timezone.utc)
    db.commit()
    db.refresh(release)
    return release


def set_market_release_policy_overrides(db: Session, release: MarketRelease, overrides: dict) -> MarketRelease:
    try:
        set_policy_overrides(release, overrides)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    db.refresh(release)
    return release
