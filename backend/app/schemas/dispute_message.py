from datetime import datetime

from app.schemas.common import CamelModel


class DisputeCaseMessageCreate(CamelModel):
    body: str
    # Only honored when the sender is an admin -- see crud/dispute_message.py:post_message.
    visibility_class: str | None = None


class DisputeCaseMessageModerate(CamelModel):
    hidden: bool


class DisputeCaseMessageRead(CamelModel):
    id: int
    case_id: int
    sender_role: str
    sender_guest_id: str | None
    sender_party_id: int | None
    sender_admin_id: int | None
    body: str
    visibility_class: str
    moderation_state: str
    moderated_by_admin_id: int | None
    moderated_at: datetime | None
    created_at: datetime
