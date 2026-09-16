"""Admin CRUD for MarketPolicyPack -- previously creatable only inside test
fixtures. Every jurisdiction-aware domain in this codebase (deposit, sublet,
rent-change, occupancy eligibility, property compliance, screening) resolves
its rules from this table via resolve_market_policy(); without a real admin
surface to create/edit these rows, none of that logic has anything to read
from in production. Restricted to super_admin throughout -- this table
governs real legal/compliance policy, not an ordinary operational record."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_super_admin
from app.crud import market_policy as crud
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.schemas.market_policy import MarketPolicyPackCreate, MarketPolicyPackRead, MarketPolicyPackUpdate

router = APIRouter(prefix="/api/market-policy-packs", tags=["market-policy-packs"], dependencies=[Depends(require_super_admin)])


@router.get("", response_model=list[MarketPolicyPackRead])
def get_market_policy_packs(
    jurisdiction_code: str | None = Query(default=None, alias="jurisdictionCode"), db: Session = Depends(get_db),
):
    return crud.list_market_policy_packs(db, jurisdiction_code)


@router.post("", response_model=MarketPolicyPackRead, status_code=status.HTTP_201_CREATED)
def post_create_market_policy_pack(
    payload: MarketPolicyPackCreate, admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    return crud.create_market_policy_pack(db, admin, payload.model_dump())


@router.get("/{pack_id}", response_model=MarketPolicyPackRead)
def get_market_policy_pack(pack_id: int, db: Session = Depends(get_db)):
    return crud.get_market_policy_pack_or_404(db, pack_id)


@router.patch("/{pack_id}", response_model=MarketPolicyPackRead)
def patch_market_policy_pack(
    pack_id: int, payload: MarketPolicyPackUpdate, admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    pack = crud.get_market_policy_pack_or_404(db, pack_id)
    return crud.update_market_policy_pack(db, admin, pack, payload.model_dump(exclude_unset=True))
