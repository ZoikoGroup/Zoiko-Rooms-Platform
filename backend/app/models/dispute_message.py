from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

DISPUTE_MESSAGE_SENDER_ROLES = ("RENTER", "HOST", "ADMIN")

# Same disclosure vocabulary as DisputeEvidenceItem's PARTY_VISIBLE/
# INTERNAL_ONLY split -- an admin can leave an internal case note that
# never reaches either party, same as an internal-only evidence note.
DISPUTE_MESSAGE_VISIBILITY_CLASSES = ("PARTY_VISIBLE", "INTERNAL_ONLY")

# Section 11 "Messages... moderation; no off-record staff decisions" --
# moderation is a visible, audited state flip (HIDDEN), never a silent
# deletion. The message row is never removed.
DISPUTE_MESSAGE_MODERATION_STATES = ("VISIBLE", "HIDDEN")


class DisputeCaseMessage(Base):
    """ZR-ENG-CLR-010 Section 11/19/23 `case_message`: structured,
    moderated case-room messaging. Section 19: 'Safety cases can disable
    party-to-party messaging' -- crud/dispute_message.py:assert_messaging_allowed
    blocks a RENTER/HOST sender outright once the case carries a
    PROTECTED_SAFETY claim or an A6 authority class; an ADMIN can always
    still post (one-way, moderated communication to either party stays
    available even when direct party-to-party messaging is disabled)."""

    __tablename__ = "dispute_case_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_role: Mapped[str] = mapped_column(String(10), nullable=False)
    sender_guest_id: Mapped[str | None] = mapped_column(ForeignKey("guests.id", ondelete="SET NULL"), nullable=True)
    sender_party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id", ondelete="SET NULL"), nullable=True)
    sender_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    body: Mapped[str] = mapped_column(String(4000), nullable=False)
    visibility_class: Mapped[str] = mapped_column(String(20), default="PARTY_VISIBLE")
    moderation_state: Mapped[str] = mapped_column(String(10), default="VISIBLE")
    moderated_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    case: Mapped["DisputeResolutionCase"] = relationship()
    sender_guest: Mapped["Guest"] = relationship(foreign_keys=[sender_guest_id])
    sender_party: Mapped["Party"] = relationship(foreign_keys=[sender_party_id])
    sender_admin: Mapped["AdminUser"] = relationship(foreign_keys=[sender_admin_id])
    moderated_by_admin: Mapped["AdminUser"] = relationship(foreign_keys=[moderated_by_admin_id])
