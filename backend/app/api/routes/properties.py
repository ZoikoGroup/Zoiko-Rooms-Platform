from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.crud.property import (
    create_property,
    create_room,
    get_property,
    list_properties_for,
    list_rooms_for_properties,
    list_rooms_for_property,
)
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.schemas.marketplace import PropertyCreate, PropertyRead, RoomCreate, RoomRead

router = APIRouter(prefix="/api/properties", tags=["properties"], dependencies=[Depends(get_current_admin)])


def _get_property_or_404(db: Session, property_id: int, admin: AdminUser):
    prop = get_property(db, property_id)
    if not prop:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Property not found")
    if admin.role != "super_admin" and prop.owner_party_id != _owner_party_id(db, admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only manage your own properties")
    return prop


def _owner_party_id(db: Session, admin: AdminUser) -> int:
    from app.crud.party import get_or_create_default_party

    return get_or_create_default_party(db, admin).id


@router.get("", response_model=list[PropertyRead])
def get_properties(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return list_properties_for(db, admin)


@router.post("", response_model=PropertyRead, status_code=status.HTTP_201_CREATED)
def post_property(payload: PropertyCreate, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return create_property(db, admin, payload)


@router.get("/rooms", response_model=list[RoomRead])
def get_rooms_bulk(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    """All rooms across every property the current admin can see, in one query --
    avoids the N+1 (one /properties/{id}/rooms call per property) pattern that
    PropertiesManager.tsx and TrustSafetyManager.tsx both previously used."""
    property_ids = [p.id for p in list_properties_for(db, admin)]
    return list_rooms_for_properties(db, property_ids)


@router.get("/{property_id}/rooms", response_model=list[RoomRead])
def get_rooms(property_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    _get_property_or_404(db, property_id, admin)
    return list_rooms_for_property(db, property_id)


@router.post("/{property_id}/rooms", response_model=RoomRead, status_code=status.HTTP_201_CREATED)
def post_room(
    property_id: int,
    payload: RoomCreate,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    prop = _get_property_or_404(db, property_id, admin)
    return create_room(db, prop, payload)
