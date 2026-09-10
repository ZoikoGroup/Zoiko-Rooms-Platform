from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

ROOM_HOLD_STATUSES = ("HELD", "BOOKED", "OCCUPIED", "RELEASED")


class RoomHold(Base):
    """ZR-ENG-CLR-001 Section 1, Rule 4 / Section 11.3: the Inventory
    Service's atomic exclusive reservation of a room.

    Adapted to this platform's whole-room, long-term-rental model
    (capacity=1 per room, no per-night calendar): a room has at most one
    *active* (unreleased) hold at a time. That exclusivity is enforced by the
    partial unique index below, not an application-level check-then-act race
    -- this is the actual atomicity guarantee Rule 4 / AC-05 require ("Two
    concurrent confirmation attempts cannot both confirm the same exclusive
    capacity... use database constraints/transactional locking").

    Created the moment a renter's offer is accepted (see
    crud/leasing.py:set_offer_status/user_accept_offer) -- the earliest point
    a room is actually committed to one applicant, well before Occupancy
    exists (Occupancy is only created at move-in). Without this, two
    different accepted offers on the same room could both reach move-in and
    both succeed. Released when the offer is declined/expired/withdrawn, or
    when the resulting occupancy ends (crud/occupancy.py:end_occupancy) --
    freeing the room for a new tenancy."""

    __tablename__ = "room_holds"
    __table_args__ = (
        Index(
            "uq_room_holds_active_room", "room_id", unique=True,
            postgresql_where=text("released_at IS NULL"),
            sqlite_where=text("released_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    # "offer" for now -- the only thing that creates a hold today. Kept
    # generic (source_type/source_id) rather than a hard offer_id FK so a
    # later hold source (e.g. a direct booking) doesn't need a schema change.
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="HELD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_reason: Mapped[str] = mapped_column(String(200), default="")

    room: Mapped["Room"] = relationship()
