from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# SUBMITTED -> IN_REVIEW -> (ACTION_REQUIRED <-> IN_REVIEW) -> RESOLVED -> CLOSED.
# A user can reopen a RESOLVED ticket (back to IN_REVIEW); CLOSED is final.
SUPPORT_TICKET_STATUSES = ("SUBMITTED", "IN_REVIEW", "ACTION_REQUIRED", "RESOLVED", "CLOSED")
SUPPORT_TICKET_CATEGORIES = (
    "apps", "rentals", "payments", "verification", "listings", "sublet", "account", "safety", "privacy", "tech",
)
# What the ticket is about, when it's about one of the user's own records.
SUPPORT_LINKED_TYPES = ("occupancy", "application", "property")
# ASSISTANT: the automatic first reply from the knowledge base (services/support_assistant.py).
SUPPORT_SENDERS = ("USER", "SUPPORT", "ASSISTANT")


class SupportTicket(Base):
    """A user's Help & Support case. `ref` (ZR-SUP-xxxxx) is what the user sees
    and what the user-facing routes are keyed by."""

    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    issue: Mapped[str] = mapped_column(String(120), nullable=False)
    linked_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    linked_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Snapshot of how the linked item read when the ticket was filed, so the
    # case still makes sense if the record is later renamed or removed.
    linked_label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    time_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reply_channel: Mapped[str] = mapped_column(String(10), nullable=False, default="both")  # "both" | "app"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="SUBMITTED", index=True)
    # Set by support with ACTION_REQUIRED: what the user needs to send.
    action_request: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set by support with RESOLVED/CLOSED: the outcome shown to the user.
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["UserAccount"] = relationship()
    messages: Mapped[list["SupportTicketMessage"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="SupportTicketMessage.id",
    )
    attachments: Mapped[list["SupportTicketAttachment"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="SupportTicketAttachment.id",
    )


class SupportTicketMessage(Base):
    __tablename__ = "support_ticket_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    sender: Mapped[str] = mapped_column(String(10), nullable=False)
    author_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    ticket: Mapped[SupportTicket] = relationship(back_populates="messages")
    attachments: Mapped[list["SupportTicketAttachment"]] = relationship(back_populates="message")


class SupportTicketAttachment(Base):
    """A file the user attached, stored through core/file_store under a random
    name; only that stored name is ever used to read it back."""

    __tablename__ = "support_ticket_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("support_ticket_messages.id", ondelete="SET NULL"), nullable=True,
    )
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    ticket: Mapped[SupportTicket] = relationship(back_populates="attachments")
    message: Mapped[SupportTicketMessage | None] = relationship(back_populates="attachments")
