from datetime import date

from app.schemas.common import CamelModel


class GuestRead(CamelModel):
    id: str
    name: str
    email: str
    phone: str
    avatar: str
    location: str
    total_bookings: int
    total_spent: float
    joined_at: date
    status: str
    # None when this guest row has no linked Zoiko login (e.g. an admin-recorded
    # walk-in) -- features keyed on Party (like Occupancy Eligibility) simply
    # can't target them yet.
    party_id: int | None = None
