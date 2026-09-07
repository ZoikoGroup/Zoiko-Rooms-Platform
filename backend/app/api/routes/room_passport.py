from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.crud.party import assert_provider_access, party_id_for_room
from app.crud.property import get_room
from app.crud.room_passport import add_claim, list_claims_for_room
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.schemas.marketplace import RoomPassportClaimCreate, RoomPassportClaimRead

router = APIRouter(prefix="/api/rooms", tags=["room-passport"], dependencies=[Depends(get_current_admin)])


def _get_owned_room_or_404(db: Session, admin: AdminUser, room_id: int):
    room = get_room(db, room_id)
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    assert_provider_access(db, admin, party_id_for_room(room))
    return room


@router.get("/{room_id}/passport/claims", response_model=list[RoomPassportClaimRead])
def get_claims(room_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    _get_owned_room_or_404(db, admin, room_id)
    return list_claims_for_room(db, room_id)


@router.post("/{room_id}/passport/claims", response_model=RoomPassportClaimRead, status_code=status.HTTP_201_CREATED)
def post_claim(
    room_id: int,
    payload: RoomPassportClaimCreate,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    room = _get_owned_room_or_404(db, admin, room_id)
    return add_claim(db, room, payload)
