from datetime import date, datetime

from app.schemas.common import CamelModel


class OccupancyRead(CamelModel):
    id: int
    offer_id: int
    listing_id: str
    listing_name: str
    room_id: int
    property_address: str
    property_city: str
    guest_id: str
    guest_name: str
    status: str
    move_in_date: date | None
    expected_end_date: date | None
    move_out_date: date | None
    # ZR-ENG-CLR-004 AC-20: distinct from move_out_date -- see
    # crud/occupancy.py:end_occupancy's own docstring.
    notice_given_at: datetime | None = None
    liability_end_date: date | None = None
    termination_effective_date: date | None = None
    created_at: datetime
    ended_at: datetime | None


class OccupancyEndRequest(CamelModel):
    """ZR-ENG-CLR-004 AC-20: optional -- when omitted, all three dates
    default to today, same as before this existed."""

    notice_given_at: datetime | None = None
    liability_end_date: date | None = None
    termination_effective_date: date | None = None
    move_out_date: date | None = None
    basis: str = "OTHER"


class TerminationRecordRead(CamelModel):
    id: int
    occupancy_id: int
    agreement_id: int
    basis: str
    notice_given_at: datetime | None = None
    liability_end_date: date | None = None
    termination_effective_date: date | None = None
    physical_move_out_date: date | None = None
    created_by_admin_id: int | None = None
    created_at: datetime
