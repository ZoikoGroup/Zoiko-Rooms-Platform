"""ZR-ENG-CLR-001 Section 1, Rule 4 / Section 11.3: the Inventory Service
domain -- exclusive room holds, atomic creation/release.

See models/room_hold.py for why this exists and how it's adapted to this
platform's whole-room, long-term-rental model (capacity=1 per room, no
per-night calendar). Listing Service (crud/listing.py) and the leasing
pipeline (crud/leasing.py, crud/occupancy.py) call through here; neither
mutates room_holds directly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.crud.audit import log_audit_event
from app.models.room_hold import RoomHold


def get_active_hold_for_room(db: Session, room_id: int) -> RoomHold | None:
    return db.scalar(
        select(RoomHold).where(RoomHold.room_id == room_id, RoomHold.released_at.is_(None))
    )


def get_hold_for_source(db: Session, *, source_type: str, source_id: int) -> RoomHold | None:
    return db.scalar(
        select(RoomHold).where(
            RoomHold.source_type == source_type,
            RoomHold.source_id == source_id,
            RoomHold.released_at.is_(None),
        )
    )


def create_hold(
    db: Session, *, room_id: int, source_type: str, source_id: int, correlation_id: str = "",
) -> RoomHold:
    """Fail-fast pre-check (fast path for the overwhelmingly common
    sequential case -- also what keeps the 409 response fast and readable)
    backed by the partial unique index on room_id (WHERE released_at IS
    NULL) as the actual atomicity guarantee for a genuine concurrent race:
    two simultaneous callers can both pass the pre-check (each in its own,
    not-yet-committed transaction), but only one of their inserts can ever
    succeed -- the loser's flush() raises IntegrityError here (7.3: "use
    database constraints/transactional locking... never a check-then-act
    race" -- AC-05, AC-10).

    Callers MUST invoke this inside a `with db.begin_nested():` block that
    also contains whatever status change led here (e.g. an offer being
    marked ACCEPTED) -- entering a SAVEPOINT flushes prior pending state
    into the *outer* transaction before the SAVEPOINT starts, so a status
    flip made before the `with` block would survive this function's failure
    even though it logically shouldn't have. See crud/leasing.py's
    set_offer_status/user_accept_offer for the required shape. This
    function itself does not roll anything back on failure -- that's the
    caller's `with` block's job, since it owns the transaction boundary."""
    if get_active_hold_for_room(db, room_id) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This room is already held or booked by another offer",
        )

    hold = RoomHold(room_id=room_id, source_type=source_type, source_id=source_id, status="HELD")
    db.add(hold)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This room is already held or booked by another offer",
        )
    # Section 15: hold creation is one of the explicitly-named audit-required
    # actions. actor=None -- this is a system-level inventory decision, not
    # an admin action; the admin/user who triggered it is already captured
    # by the caller's own audit event (e.g. "offer.accept") sharing the same
    # correlation_id.
    log_audit_event(
        db, None, "room_hold.create", "room_hold", str(hold.id), correlation_id,
        reason=f"{source_type}:{source_id}",
    )
    return hold


def mark_hold_booked(db: Session, *, source_type: str, source_id: int) -> None:
    """No-op if no hold exists -- callers that construct leasing state
    directly (bypassing offer acceptance) never created one, and that's not
    this function's problem to enforce."""
    hold = get_hold_for_source(db, source_type=source_type, source_id=source_id)
    if hold is not None:
        hold.status = "BOOKED"


def mark_hold_occupied(db: Session, *, source_type: str, source_id: int) -> None:
    hold = get_hold_for_source(db, source_type=source_type, source_id=source_id)
    if hold is not None:
        hold.status = "OCCUPIED"


def release_hold(
    db: Session, *, source_type: str, source_id: int, reason: str, correlation_id: str = "",
) -> None:
    """Idempotent (7.3: "All hold creation/release operations must be
    idempotent") -- releasing an already-released or nonexistent hold is a
    no-op, not an error."""
    hold = get_hold_for_source(db, source_type=source_type, source_id=source_id)
    if hold is None:
        return
    hold.released_at = datetime.now(timezone.utc)
    hold.release_reason = reason
    hold.status = "RELEASED"
    log_audit_event(db, None, "room_hold.release", "room_hold", str(hold.id), correlation_id, reason=reason)
