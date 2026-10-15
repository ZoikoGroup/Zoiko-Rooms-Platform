from datetime import datetime

from pydantic import Field

from app.schemas.common import CamelModel


class SupportAttachmentRead(CamelModel):
    id: int
    message_id: int | None
    original_filename: str
    content_type: str
    size: int
    created_at: datetime


class SupportMessageRead(CamelModel):
    id: int
    sender: str  # "USER" | "SUPPORT" | "ASSISTANT"
    body: str
    created_at: datetime
    attachments: list[SupportAttachmentRead] = []


class SupportTicketRead(CamelModel):
    ref: str
    category: str
    issue: str
    linked_type: str | None
    linked_id: int | None
    linked_label: str
    description: str
    time_sensitive: bool
    reply_channel: str
    status: str
    action_request: str | None
    resolution: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    messages: list[SupportMessageRead] = []
    attachments: list[SupportAttachmentRead] = []


class AdminSupportTicketRead(SupportTicketRead):
    id: int
    user_id: int
    user_email: str = ""
    user_name: str = ""
    assigned_admin_id: int | None = None


class SupportReplyCreate(CamelModel):
    body: str = Field(min_length=1, max_length=5000)


class AdminSupportStatusUpdate(CamelModel):
    """Support moves a ticket along. ACTION_REQUIRED needs action_request
    (what the user must send); RESOLVED/CLOSED need resolution."""

    status: str
    action_request: str | None = Field(default=None, max_length=2000)
    resolution: str | None = Field(default=None, max_length=5000)
