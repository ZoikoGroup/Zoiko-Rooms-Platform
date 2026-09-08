from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_super_admin
from app.core.correlation import get_correlation_id
from app.crud.audit import log_audit_event
from app.crud.occupancy_classification import (
    get_classification_for_room,
    list_classifications_for_rooms,
    set_classification,
)
from app.crud.property import get_room
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.schemas.marketplace import OccupancyClassificationRead, OccupancyClassificationSet

router = APIRouter(prefix="/api/rooms", tags=["occupancy-classification"], dependencies=[Depends(get_current_admin)])


@router.get("/occupancy-classifications", response_model=list[OccupancyClassificationRead])
def get_classifications_bulk(room_ids: str, db: Session = Depends(get_db)):
    """Bulk variant of GET /{room_id}/occupancy-classification -- one query for N
    rooms instead of N requests. room_ids is a comma-separated list of room ids."""
    parsed_ids = [int(r) for r in room_ids.split(",") if r.strip()]
    return list_classifications_for_rooms(db, parsed_ids)


@router.get("/{room_id}/occupancy-classification", response_model=OccupancyClassificationRead | None)
def get_classification(room_id: int, db: Session = Depends(get_db)):
    if not get_room(db, room_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    return get_classification_for_room(db, room_id)


@router.put(
    "/{room_id}/occupancy-classification",
    response_model=OccupancyClassificationRead,
    dependencies=[Depends(require_super_admin)],
)
def put_classification(
    room_id: int,
    payload: OccupancyClassificationSet,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    room = get_room(db, room_id)
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    updated = set_classification(db, room, payload)
    log_audit_event(db, admin, "occupancy_classification.set", "room", str(room_id), get_correlation_id(request))
    db.commit()
    return updated
