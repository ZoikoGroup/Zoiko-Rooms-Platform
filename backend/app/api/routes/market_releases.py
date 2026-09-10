from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import require_super_admin
from app.core.correlation import get_correlation_id
from app.crud.audit import log_audit_event
from app.crud.market_release import (
    create_market_release,
    get_market_release,
    list_market_releases,
    set_market_release_policy_overrides,
    set_market_release_status,
)
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.schemas.marketplace import MarketReleaseCreate, MarketReleasePolicyUpdate, MarketReleaseRead

router = APIRouter(prefix="/api/market-releases", tags=["market-releases"], dependencies=[Depends(require_super_admin)])


def _get_or_404(db: Session, market_release_id: int):
    release = get_market_release(db, market_release_id)
    if not release:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Market release not found")
    return release


@router.get("", response_model=list[MarketReleaseRead])
def get_market_releases(db: Session = Depends(get_db)):
    return list_market_releases(db)


@router.post("", response_model=MarketReleaseRead, status_code=status.HTTP_201_CREATED)
def post_market_release(payload: MarketReleaseCreate, db: Session = Depends(get_db)):
    return create_market_release(db, payload)


@router.post("/{market_release_id}/approve", response_model=MarketReleaseRead)
def approve_market_release(
    market_release_id: int,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    release = _get_or_404(db, market_release_id)
    updated = set_market_release_status(db, release, "active", admin)
    log_audit_event(db, admin, "market_release.approve", "market_release", str(market_release_id), get_correlation_id(request))
    db.commit()
    return updated


@router.post("/{market_release_id}/disable", response_model=MarketReleaseRead)
def disable_market_release(
    market_release_id: int,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    release = _get_or_404(db, market_release_id)
    updated = set_market_release_status(db, release, "disabled", admin)
    log_audit_event(db, admin, "market_release.disable", "market_release", str(market_release_id), get_correlation_id(request))
    db.commit()
    return updated


@router.put("/{market_release_id}/policy", response_model=MarketReleaseRead)
def put_market_release_policy(
    market_release_id: int,
    payload: MarketReleasePolicyUpdate,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-001 Section 14: replace this market's full policy-override
    set (see app/services/policy.py for the known keys). Super admin only --
    same authority level as approve/disable above."""
    release = _get_or_404(db, market_release_id)
    updated = set_market_release_policy_overrides(db, release, payload.overrides)
    log_audit_event(
        db, admin, "market_release.set_policy", "market_release", str(market_release_id), get_correlation_id(request),
        reason=str(payload.overrides),
    )
    db.commit()
    return updated
