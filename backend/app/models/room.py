from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Private-room-only taxonomy per the marketplace standard -- whole-home, studio, hotel,
# nightly/vacation, hostel and dorm/shared-bed inventory is explicitly rejected.
ROOM_TYPES = ("private_room",)
ROOM_STATUSES = ("active", "inactive")


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    room_type: Mapped[str] = mapped_column(String(30), default="private_room")
    size: Mapped[int] = mapped_column(Integer, default=0)
    has_ensuite: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="active")
    # How many concurrent ACTIVE/PENDING_MOVE_IN occupancies this room may hold.
    # Default 1 matches every room's behavior before this column existed --
    # nothing previously checked this at all, so two tenancies could silently
    # double-book the same room. Raise this only for rooms actually meant to be
    # shared (ZR-ENG-CLR-003 Section 3's SUBLEASE_PARTIAL / ADD_CO_TENANT).
    max_occupants: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    property: Mapped["Property"] = relationship(back_populates="rooms")
    authority_records: Mapped[list["AuthorityRecord"]] = relationship(back_populates="room", cascade="all, delete-orphan")
    passport_claims: Mapped[list["RoomPassportClaim"]] = relationship(back_populates="room", cascade="all, delete-orphan")
    passport_snapshots: Mapped[list["RoomPassportSnapshot"]] = relationship(back_populates="room", cascade="all, delete-orphan")
    occupancy_classification: Mapped["OccupancyClassification"] = relationship(
        back_populates="room", uselist=False, cascade="all, delete-orphan"
    )
    listings: Mapped[list["Listing"]] = relationship(back_populates="room")
