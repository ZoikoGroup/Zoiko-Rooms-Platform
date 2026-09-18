from datetime import date, datetime

from app.schemas.common import CamelModel


# Shared with schemas/leasing.py -- the real leasing pipeline's admin-recorded
# walk-in-guest applications reuse this shape, not just the legacy Booking
# model below.
class NewGuestInput(CamelModel):
    name: str
    email: str
    phone: str = ""
    location: str = ""


class BookingRead(CamelModel):
    id: str
    listing_id: str
    listing_name: str
    property_type: str
    guest_name: str
    guest_email: str
    guest_avatar: str
    check_in: date
    check_out: date
    nights: int
    guests: int
    total_amount: float
    status: str
    payment_status: str
    created_at: datetime
