"""Request/response schemas for the authenticated-free public assistant.

Deliberately narrow: the public endpoint accepts one user turn plus an
optional, client-held history. The history is untrusted input (the server
re-caps and sanitises it) and the session id is an opaque correlation token,
never an authorization credential.
"""

from app.schemas.common import CamelModel


class PublicAssistantHistoryItem(CamelModel):
    role: str
    content: str


class PublicAssistantTurn(CamelModel):
    message: str
    session_id: str | None = None
    # Client-held context only. The server caps this to the last few exchanges
    # regardless of what is sent; it is never treated as validated prior output.
    history: list[PublicAssistantHistoryItem] = []


class PublicCitation(CamelModel):
    citation_id: str
    source_type: str = "KNOWLEDGE"
    source_id: int | None = None
    source_version: str = ""
    section: str = ""
    market: str = ""
    effective_at: str = ""


class PublicAssistantReply(CamelModel):
    answer: str
    citations: list[PublicCitation] = []
    session_id: str = ""
    risk: str = ""
    risk_topic: str = ""
    action_tier: str = ""
    determination_blocked: bool = False