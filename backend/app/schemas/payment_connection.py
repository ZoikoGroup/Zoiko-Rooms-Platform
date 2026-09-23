from datetime import datetime

from app.schemas.common import CamelModel


class PaymentConnectionRead(CamelModel):
    """ZR-PAY-LINK-003 Section 3.1 -- crud/payment_connection.py's own
    consolidated view. recipient_*/destination_* fields are null when there
    is nothing yet to report (e.g. DRAFT has no authority row at all)."""

    room_id: int
    state: str
    recipient_party_id: int | None
    recipient_authority_id: int | None
    recipient_relationship_type: str | None
    recipient_authority_status: str | None
    recipient_verified_at: datetime | None
    recipient_expires_at: datetime | None
    destination_method: str | None
    destination_status: str | None
    destination_account_identifier_masked: str | None
