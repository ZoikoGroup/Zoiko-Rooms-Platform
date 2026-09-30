from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.occupancy_classification import OccupancyClassification
from app.models.room import Room
from app.schemas.marketplace import OccupancyClassificationSet


def get_classification_for_room(db: Session, room_id: int) -> OccupancyClassification | None:
    return db.scalar(select(OccupancyClassification).where(OccupancyClassification.room_id == room_id))


# Every room gets this classification automatically, so a missing one never
# blocks the agreement pipeline. A super admin can still change it any time
# through set_classification (Trust & Safety -> Occupancy Classification).
DEFAULT_CLASSIFICATION = "shared_residential_room"


def ensure_default_classification(db: Session, room: Room) -> OccupancyClassification:
    """The room's classification, creating the default APPROVED one if it has
    none yet. Never overwrites an existing (admin-set) classification."""
    record = get_classification_for_room(db, room.id)
    if record is not None:
        return record
    record = OccupancyClassification(
        room_id=room.id, rule_version=1, classification=DEFAULT_CLASSIFICATION,
        confidence=1.0, evidence_ref="auto-default", review_state="APPROVED",
        jurisdiction=room.property.jurisdiction_code or room.property.owner_party.jurisdiction,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


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

    # A resolved classification can be the last gate an accepted offer was waiting on.
    from app.crud.leasing import retry_pending_auto_agreements

    retry_pending_auto_agreements(db)
    db.refresh(record)
    return record
