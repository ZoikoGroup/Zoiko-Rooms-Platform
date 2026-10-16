from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

RENTAL_MESSAGE_SENDER_ROLES = ("RENTER", "HOST")


class RentalConversation(Base):
    """Host <-> renter messaging, one thread per application per renter.
    Created on the first message from either side; carries on through offer,
    agreement and tenancy since all of those hang off the same application.
    guest_id is the renter side of the thread: the applicant, or -- after a
    full sublet handover -- the incoming tenant, who gets their own thread
    rather than seeing the previous renter's. Admins have no write path."""

    __tablename__ = "rental_conversations"
    __table_args__ = (UniqueConstraint("application_id", "guest_id", name="uq_rental_conversations_application_guest"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    guest_id: Mapped[str] = mapped_column(String(20), ForeignKey("guests.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    application: Mapped["Application"] = relationship()
    guest: Mapped["Guest"] = relationship()
    messages: Mapped[list["RentalMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="RentalMessage.id",
    )


class RentalMessage(Base):
    __tablename__ = "rental_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("rental_conversations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    sender_role: Mapped[str] = mapped_column(String(10), nullable=False)
    sender_user_id: Mapped[int | None] = mapped_column(ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # Set when the other side opens the thread.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped[RentalConversation] = relationship(back_populates="messages")
