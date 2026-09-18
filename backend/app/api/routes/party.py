from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import require_super_admin
from app.core.correlation import get_correlation_id
from app.crud.audit import log_audit_event
from app.crud.party import set_party_jurisdiction
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.party import Party
from app.schemas.marketplace import PartyJurisdictionUpdate, PartyRead

router = APIRouter(prefix="/api/parties", tags=["parties"], dependencies=[Depends(require_super_admin)])


def _get_or_404(db: Session, party_id: int) -> Party:
    party = db.get(Party, party_id)
    if not party:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Party not found")
    return party


@router.get("/{party_id}", response_model=PartyRead)
def get_party(party_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, party_id)


@router.put("/{party_id}/jurisdiction", response_model=PartyRead)
def put_party_jurisdiction(
    party_id: int,
    payload: PartyJurisdictionUpdate,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Which jurisdiction this provider's future listings resolve their MarketRelease
    against (see crud/listing.py:_resolve_market_release_id_for_room). Super admin
    only, same authority level as approving a market release -- this is a
    legal/compliance routing decision, not a provider self-service setting."""
    party = _get_or_404(db, party_id)
    updated = set_party_jurisdiction(db, party, payload.jurisdiction)
    log_audit_event(
        db, admin, "party.set_jurisdiction", "party", str(party_id), get_correlation_id(request),
        reason=payload.jurisdiction,
    )
    db.commit()
    return updated
