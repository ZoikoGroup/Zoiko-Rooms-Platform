"""Local test data: one more room, ready for the Listing Fee.

Adds a second room to an existing host's verified property, copies that
property's first room's verified records onto it (property verification,
authority record, occupancy classification, compliance credentials,
payment-recipient authority), and creates a DRAFT listing for it. Every
publish requirement except the fee is then met, so the host can go
straight through the Listing Fee checkout.

    python seed_listing_fee_test_room.py [--source-room 1]

Idempotent: does nothing if the test listing already exists. Local/dev
data only -- never run against production.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy import String, inspect, select

from app.core.config import settings
from app.crud.listing import check_publish_eligibility
from app.db.session import SessionLocal
from app.models.authority_record import AuthorityRecord
from app.models.listing import Listing
from app.models.occupancy_classification import OccupancyClassification
from app.models.payment_recipient_authority import PaymentRecipientAuthority
from app.models.property_compliance_credential import PropertyComplianceCredential
from app.models.property_verification import PropertyVerification
from app.models.room import Room

TEST_LISTING_ID = "L-FEETEST01"
PER_ROOM_MODELS = (
    PropertyVerification,
    AuthorityRecord,
    OccupancyClassification,
    PropertyComplianceCredential,
    PaymentRecipientAuthority,
)


def _clone(db, row, **overrides):
    """Copies every column except the primary key. Unique text columns get
    a suffix so the copy doesn't collide with the original."""
    mapper = inspect(type(row))
    values = {}
    for attr in mapper.column_attrs:
        column = attr.columns[0]
        if column.primary_key:
            continue
        value = getattr(row, attr.key)
        if column.unique and isinstance(column.type, String) and isinstance(value, str) and value:
            suffix = f"-{overrides.get('room_id', 'copy')}"
            limit = column.type.length or 255
            value = (value[: limit - len(suffix)] + suffix)
        values[attr.key] = value
    values.update(overrides)
    copy = type(row)(**values)
    db.add(copy)
    return copy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-room", type=int, default=1)
    args = parser.parse_args()

    if settings.environment == "production":
        raise SystemExit("Refusing to seed test data in production.")

    db = SessionLocal()
    try:
        if db.get(Listing, TEST_LISTING_ID):
            print(f"{TEST_LISTING_ID} already exists -- nothing to do.")
            return

        source_room = db.get(Room, args.source_room)
        if source_room is None:
            raise SystemExit(f"Room {args.source_room} not found.")
        source_listing = db.scalar(select(Listing).where(Listing.room_id == source_room.id).order_by(Listing.id))
        if source_listing is None:
            raise SystemExit(f"Room {args.source_room} has no listing to copy details from.")

        room = _clone(db, source_room)
        db.flush()
        for model in PER_ROOM_MODELS:
            for row in db.scalars(select(model).where(model.room_id == source_room.id)):
                _clone(db, row, room_id=room.id)
        db.flush()

        now = datetime.now(timezone.utc)
        listing = _clone(
            db, source_listing,
            slug=f"{source_listing.slug}-room-{room.id}",
            name=f"{source_listing.name} - Room {room.id}",
            room_id=room.id, state="DRAFT", published_at=None,
            featured=False, rating=0, review_count=0,
        )
        listing.id = TEST_LISTING_ID
        for stamp in ("created_at", "updated_at"):
            if hasattr(listing, stamp):
                setattr(listing, stamp, now)
        db.flush()

        reasons = check_publish_eligibility(db, listing)
        db.commit()
        print(f"Room {room.id} and draft listing {listing.id} ('{listing.name}') created for party {listing.party_id}.")
        print("Still open before publishing:", reasons or "nothing")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
