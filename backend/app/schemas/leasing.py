from datetime import date, datetime, timezone

from pydantic import field_validator

from app.schemas.booking import NewGuestInput
from app.schemas.common import CamelModel


def _reject_past_date(value: date | None) -> date | None:
    if value is not None and value < datetime.now(timezone.utc).date():
        raise ValueError("Desired move-in date cannot be in the past")
    return value


class ApplicationCreate(CamelModel):
    listing_id: str
    guest_id: str | None = None
    new_guest: NewGuestInput | None = None
    message: str = ""
    desired_move_in: date | None = None

    _validate_desired_move_in = field_validator("desired_move_in")(_reject_past_date)


class ApplicationDecisionRead(CamelModel):
    id: int
    decision: str
    reason_code: str
    note: str
    decided_by_admin_id: int
    decided_at: datetime


class ApplicationDecide(CamelModel):
    decision: str
    reason_code: str = ""
    note: str = ""


class ApplicationUpdate(CamelModel):
    message: str | None = None
    desired_move_in: date | None = None


class OfferTermsCreate(CamelModel):
    monthly_rent: float
    deposit_amount: float
    start_date: date
    term_months: int


class OfferTermsRead(CamelModel):
    id: int
    version: int
    monthly_rent: float
    deposit_amount: float
    start_date: date
    term_months: int
    created_at: datetime


class AgreementRead(CamelModel):
    id: int
    offer_id: int
    version: int
    status: str
    content_ref: str
    signed_by_provider_at: datetime | None
    signed_by_renter_at: datetime | None
    signature_ref: str
    created_at: datetime


class AgreementSign(CamelModel):
    as_party: str  # "provider" | "renter"


class OfferRead(CamelModel):
    id: int
    application_id: int
    listing_id: str
    guest_id: str
    status: str
    current_version: int
    created_at: datetime
    terms: list[OfferTermsRead] = []
    agreement: AgreementRead | None = None


class ApplicationRead(CamelModel):
    id: int
    listing_id: str
    listing_name: str = ""
    guest_id: str
    guest_name: str
    guest_email: str
    status: str
    message: str
    desired_move_in: date | None
    submitted_at: datetime
    updated_at: datetime
    decisions: list[ApplicationDecisionRead] = []
    offer: OfferRead | None = None


class UserApplicationSubmitRequest(CamelModel):
    """User submitting a rental application."""

    listing_id: str
    message: str = ""
    desired_move_in: date | None = None

    _validate_desired_move_in = field_validator("desired_move_in")(_reject_past_date)


class UserApplicationRead(CamelModel):
    """User-facing application view."""

    id: int
    listing_id: str
    listing_name: str = ""
    property_address: str = ""
    property_city: str = ""
    host_name: str = ""
    status: str
    # The real outcome once status is "DECIDED" -- "DECIDED" alone renders
    # identically for an approval and a rejection, which is genuinely
    # uninformative to the applicant. None while still SUBMITTED/WITHDRAWN.
    decision: str | None = None
    message: str
    desired_move_in: date | None
    submitted_at: datetime
    updated_at: datetime


class UserOccupancyRead(CamelModel):
    """User-facing occupancy/rental view."""

    id: int
    listing_id: str
    listing_name: str = ""
    room_id: int
    property_address: str = ""
    property_city: str = ""
    host_name: str = ""
    status: str
    move_in_date: date | None
    expected_end_date: date | None
    move_out_date: date | None
    requested_move_out_date: date | None
    move_out_requested_at: datetime | None
    created_at: datetime
    ended_at: datetime | None


class RequestMoveOut(CamelModel):
    """Renter's own move-out notice."""

    desired_move_out_date: date


class SubletRequestCreate(CamelModel):
    """User submitting a sublet request."""

    occupancy_id: int
    proposed_renter_party_id: int
    authority_evidence_ref: str = ""


class SubletRequestRead(CamelModel):
    """Read view for sublet request."""

    id: int
    current_occupancy_id: int
    proposed_renter_party_id: int
    status: str
    authority_evidence_ref: str
    admin_decision: str
    admin_notes: str
    decided_by_admin_id: int | None
    created_at: datetime
    decided_at: datetime | None
    # Resolved server-side (not stored columns) so both the requesting renter's
    # own view and the admin review queue can be searched/displayed by name
    # instead of raw ids.
    listing_name: str = ""
    property_address: str = ""
    current_renter_name: str = ""
    proposed_renter_name: str = ""


class SubletRequestDecision(CamelModel):
    """Optional review notes recorded with a sublet approval or rejection."""

    notes: str = ""
