from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.party import get_or_create_default_party
from app.models.admin_user import AdminUser
from app.models.property import Property
from app.models.room import Room
from app.schemas.marketplace import PropertyCreate, RoomCreate


def list_properties_for(db: Session, admin: AdminUser) -> list[Property]:
    query = select(Property).order_by(Property.id)
    if admin.role != "super_admin":
        party = get_or_create_default_party(db, admin)
        query = query.where(Property.owner_party_id == party.id)
    return list(db.scalars(query))


def get_property(db: Session, property_id: int) -> Property | None:
    return db.get(Property, property_id)


def create_property(db: Session, admin: AdminUser, data: PropertyCreate) -> Property:
    party = get_or_create_default_party(db, admin)
    prop = Property(owner_party_id=party.id, address=data.address, city=data.city)
    db.add(prop)
    db.commit()
    db.refresh(prop)
    return prop


def list_rooms_for_property(db: Session, property_id: int) -> list[Room]:
    return list(db.scalars(select(Room).where(Room.property_id == property_id).order_by(Room.id)))


def list_rooms_for_properties(db: Session, property_ids: list[int]) -> list[Room]:
    """Bulk variant of list_rooms_for_property -- one query for N properties'
    rooms instead of N queries. RoomRead.property_id lets the caller bucket the
    flat result client-side."""
    if not property_ids:
        return []
    return list(
        db.scalars(select(Room).where(Room.property_id.in_(property_ids)).order_by(Room.property_id, Room.id))
    )


def get_room(db: Session, room_id: int) -> Room | None:
    return db.get(Room, room_id)


def create_room(db: Session, prop: Property, data: RoomCreate) -> Room:
    room = Room(property_id=prop.id, size=data.size, has_ensuite=data.has_ensuite)
    db.add(room)
    db.commit()
    db.refresh(room)
    return room
