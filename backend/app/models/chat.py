from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ChatConversation(Base):
    """Chatbot conversation. admin_id is set for admin-side chats, user_id for user-side chats."""

    __tablename__ = "chat_conversations"
    __table_args__ = (
        CheckConstraint(
            "(admin_id IS NOT NULL AND user_id IS NULL) OR (admin_id IS NULL AND user_id IS NOT NULL)",
            name="ck_chat_conversations_one_actor",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user_accounts.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.id",
    )


class ChatMessage(Base):
    """A single message in an admin chatbot conversation.

    role is "user" or "assistant" (the roles the Anthropic Messages API accepts).
    tool_calls_json / tool_results_json preserve function-calling traffic so a
    turn can be replayed without re-executing tools.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls_json: Mapped[str] = mapped_column(Text, default="")
    tool_results_json: Mapped[str] = mapped_column(Text, default="")
    meta_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    conversation: Mapped[ChatConversation] = relationship(back_populates="messages")
