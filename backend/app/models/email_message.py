"""ZR-COMMS-EMAIL-001 Section 1.3 -- one auditable delivery record per email.

Records which approved template version went to whom, on which stream, why
(related entity) and what happened (status, attempts, provider). The dedupe
key makes a replayed event or a double-fired trigger send nothing the second
time. Deliberately stores no rendered body or subject -- only a content hash
-- per Section 3.3: "sensitive payload values should not be duplicated into
communication logs"; the template id/version plus the domain record are
enough to reproduce what was sent."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# PENDING: claimed, being sent. SENT: accepted by the SMTP server. OUTBOX:
# written to the local dev outbox (EMAIL_PROVIDER != smtp). FAILED: every
# attempt failed -- the same dedupe key may be tried again later.
# SUPPRESSED: a duplicate of an already sent/pending message; never sent.
EMAIL_MESSAGE_STATUSES = ("PENDING", "SENT", "OUTBOX", "FAILED", "SUPPRESSED")


class EmailMessage(Base):
    __tablename__ = "email_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    template_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    template_version: Mapped[str] = mapped_column(String(20), nullable=False)
    variant: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    stream: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Unique when set: the second claim of the same key is suppressed.
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("email_messages.id"), nullable=True)
    related_entity_type: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    related_entity_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    last_error: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
