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
    # ZR-SUB-003 Section 3: an occupancy that already changed hands once via
    # ASSIGNMENT_FULL/REPLACEMENT_OCCUPANT can never be sublet onward again
    # (crud/sublet.py:_assert_sublet_permitted already enforces this) -- this
    # surfaces that same fact to the UI instead of leaving it undiscoverable
    # outside the raw sublet_requests table.
    reassigned_via_sublet_request_id: int | None = None


class OccupancyEndRequest(CamelModel):
    """ZR-ENG-CLR-004 AC-20: optional -- when omitted, all three dates
    default to today, same as before this existed."""

    notice_given_at: datetime | None = None
    liability_end_date: date | None = None
    termination_effective_date: date | None = None
    move_out_date: date | None = None
    basis: str = "OTHER"
    # ZR-ENG-CLR-006 Section 7.1 Step 11: links this action back to the
    # renter's own termination_case, when finalizing one.
    termination_case_id: int | None = None
    # ZR-ENG-CLR-006 AC-05/AC-29: required (and only meaningful) when ending
    # an active occupancy early with no termination_case_id -- a Super
    # Admin's own logged override reason.
    override_reason: str = ""


class PreMoveInCancellationRequest(CamelModel):
    reason: str = ""


class ConditionReportItemRead(CamelModel):
    id: int
    occupancy_id: int
    report_type: str
    area: str
    condition_rating: str | None
    notes: str
    original_filename: str
    content_type: str
    size_bytes: int
    recorded_by_guest_id: str | None
    recorded_by_admin_id: int | None
    created_at: datetime
    has_file: bool = False


class PreMoveInCancellationRead(CamelModel):
    occupancy: OccupancyRead
    fee_amount: float
    fee_note: str
    refunded_amount: float


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


class OccupancyCoTenantCreate(CamelModel):
    """ZR-ENG-CLR-006 AC-25: adds an additional tenant to an occupancy
    beyond its own guest_id -- see models/occupancy.py:OccupancyCoTenant's
    own docstring for what this changes about termination routing."""

    guest_id: str


class OccupancyCoTenantRead(CamelModel):
    id: int
    occupancy_id: int
    guest_id: str
    added_by_admin_id: int | None
    added_at: datetime
