from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Controlled taxonomies (ZR-AI-* handoff spec / ERD ai_handoff).
# Urgency: NORMAL / HIGH / SAFETY_CRITICAL.
HANDOFF_URGENCY = ("NORMAL", "HIGH", "SAFETY_CRITICAL")
# Status: REQUESTED / ROUTED / ACCEPTED / CLOSED / FAILED.
HANDOFF_STATUS = ("REQUESTED", "ROUTED", "ACCEPTED", "CLOSED", "FAILED")
HANDOFF_ACTIVE_STATUS = ("REQUESTED", "ROUTED", "ACCEPTED")


class AiHandoff(Base):
    """Assistant-to-human support/escalation state pointer (ai_handoff).

    A handoff is a *request* owned by the Assistant; the external support/case
    system owns actual case creation. ``support_case_ref`` therefore stays NULL
    until an authoritative system confirms a case exists -- we never fabricate a
    ticket reference (FRS-HO-004). ``shared_context_manifest`` is the explicit
    allow-list of the minimum-necessary context handed to support (FRS-HO-001/-002).
    """

    __tablename__ = "ai_handoffs"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    initiator_user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id"), nullable=False, index=True
    )
    reason_code: Mapped[str] = mapped_column(String(50), nullable=False)
    urgency: Mapped[str] = mapped_column(String(20), nullable=False, default="NORMAL")
    support_case_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    shared_context_manifest: Mapped[str] = mapped_column(Text, default="{}")
    summary: Mapped[str] = mapped_column(String(1000), default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="REQUESTED")
    consent_state: Mapped[str] = mapped_column(String(30), default="NOTICE_GIVEN")
    bridge_messages_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
