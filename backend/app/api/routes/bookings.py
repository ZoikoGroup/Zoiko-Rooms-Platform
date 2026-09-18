from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_super_admin
from app.crud.booking import list_bookings
from app.db.session import get_db
from app.schemas.booking import BookingRead

router = APIRouter(prefix="/api/bookings", tags=["bookings"], dependencies=[Depends(require_super_admin)])


@router.get("", response_model=list[BookingRead])
def get_bookings(db: Session = Depends(get_db)):
    """Read-only historical record -- ZR-ENG-CLR-001 Section 1's own
    architecture (published listing -> Application -> Offer -> Agreement ->
    Occupancy, with RoomHold/jurisdiction/overlap/identity gates) is the one
    real booking pipeline. This legacy hotel-style Booking model predates
    that pipeline and creating new rows here would silently bypass every
    gate the real pipeline enforces -- so POST is intentionally removed,
    keeping this endpoint for visibility into whatever historical rows
    already exist."""
    return list_bookings(db)
