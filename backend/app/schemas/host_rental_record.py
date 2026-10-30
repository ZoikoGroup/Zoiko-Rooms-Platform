from datetime import date, datetime

from app.schemas.common import CamelModel


class RentalRecordPaymentSummary(CamelModel):
    currency: str
    paid_count: int
    open_count: int
    overdue_count: int
    overdue_amount: float
    disputed_count: int
    next_due_date: date | None
    next_due_amount: float | None
    next_due_status: str | None


class RentalRecordNotice(CamelModel):
    # "MOVE_OUT_NOTICE" (renter's handover notice) or "TERMINATION_CASE".
    kind: str
    initiated_by: str  # "RENTER" | "HOST"
    given_at: datetime
    effective_date: date | None
    status: str
    termination_case_id: int | None = None
    cause_code: str | None = None
    # When the receiving side confirmed they got the notice (None = not yet).
    acknowledged_at: datetime | None = None
    # AWAITING_RESPONSE: who still has to answer, and by when.
    pending_responders: list[str] = []
    response_due_at: datetime | None = None


class RentalRecordSublet(CamelModel):
    id: int
    status: str
    created_at: datetime


class HostRentalRecordRead(CamelModel):
    """One rental, from accepted offer through to the end of the tenancy.
    `ref` is ZR-R-<occupancy> once an occupancy exists, ZR-A-<application>
    before that. Detail (payments, timeline, documents) comes from the
    occupancy's transaction record."""

    ref: str
    occupancy_id: int | None
    application_id: int
    offer_id: int
    agreement_id: int | None
    listing_id: str
    listing_name: str
    property_id: int | None
    property_address: str
    room_id: int | None
    room_type: str
    has_ensuite: bool
    renters: list[str]
    # pending | upcoming | active | notice | ending | ended | cancelled | terminated
    status: str
    occupancy_status: str | None
    offer_status: str
    agreement_status: str | None
    agreement_version: int | None
    host_signed_at: datetime | None
    renter_signed_at: datetime | None
    monthly_rent: float | None
    deposit_amount: float | None
    currency: str
    term_months: int | None
    cadence: str | None
    start_date: date | None
    end_date: date | None
    move_out_date: date | None
    # SIGN_AGREEMENT | AWAITING_RENTER_SIGNATURE | PREPARE_MOVE_IN | REVIEW_NOTICE |
    # RESPOND_SURRENDER | RESPOND_END_DATE | CONFIRM_MOVE_OUT | REVIEW_PAYMENTS | NONE
    next_action: str
    payments: RentalRecordPaymentSummary | None
    notice: RentalRecordNotice | None
    sublet: RentalRecordSublet | None
    updated_at: datetime
