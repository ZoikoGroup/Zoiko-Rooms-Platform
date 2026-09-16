from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.occupancy_classification import OccupancyClassification
from app.models.room import Room
from app.schemas.marketplace import OccupancyClassificationSet


def get_classification_for_room(db: Session, room_id: int) -> OccupancyClassification | None:
    return db.scalar(select(OccupancyClassification).where(OccupancyClassification.room_id == room_id))


def set_classification(db: Session, room: Room, data: OccupancyClassificationSet) -> OccupancyClassification:
    record = get_classification_for_room(db, room.id)
    if not record:
        record = OccupancyClassification(room_id=room.id, rule_version=1)
        db.add(record)

    record.classification = data.classification
    record.confidence = data.confidence
    record.evidence_ref = data.evidence_ref
    record.review_state = data.review_state
    # Same source as crud/listing.py:_resolve_market_release_id_for_room -- otherwise
    # this stays stuck on the column default ("IN") regardless of which jurisdiction
    # the room is actually in.
    record.jurisdiction = room.property.owner_party.jurisdiction
    record.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(record)
    return record
