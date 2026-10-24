from datetime import datetime

from pydantic import Field

from app.schemas.common import CamelModel


class RentalMessageCreate(CamelModel):
    body: str = Field(min_length=1, max_length=2000)


class RentalMessageRead(CamelModel):
    id: int
    sender_role: str  # "RENTER" | "HOST"
    body: str
    created_at: datetime
    read_at: datetime | None
    # True when the caller sent this message -- lets the UI align the bubble.
    mine: bool


class RenterThreadRead(CamelModel):
    """One renter the host can message on an application."""

    guest_id: str
    name: str
    role: str  # "APPLICANT" | "SUBTENANT"
    is_current_occupant: bool
    unread_count: int
    last_message_at: datetime | None


class RentalConversationRead(CamelModel):
    application_id: int
    # The renter side of this thread (a subtenant has their own thread).
    guest_id: str
    listing_id: str
    listing_name: str
    # The other side: the host's name for a renter, the renter's for a host.
    counterpart_name: str
    last_message_at: datetime | None
    last_message_preview: str
    unread_count: int
