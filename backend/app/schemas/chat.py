from datetime import datetime
from typing import Literal

from app.schemas.common import CamelModel


class ChatConversationRead(CamelModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class ChatMessageRead(CamelModel):
    id: int
    role: str
    content: str
    created_at: datetime


class ChatSendMessageRequest(CamelModel):
    content: str
    # Language the assistant should reply in (the chat's own picker); None keeps the default.
    language: Literal["EN", "DE", "FR", "ES", "PT"] | None = None
