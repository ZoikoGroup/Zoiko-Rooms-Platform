from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HostSubletPolicy(Base):
    """A host's own sublet rules for their properties (Sublet Requests >
    Sublet policy settings). Only the minimum-notice rule is the host's choice:
    identity verification of the proposed subtenant and the agent-authority
    checks are platform requirements and can't be switched off here. No row
    means no host rule is set. Applied when a renter submits a sublet request
    (crud/sublet.py:submit_sublet_request)."""

    __tablename__ = "host_sublet_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), unique=True, nullable=False)
    min_notice_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    min_notice_days: Mapped[int] = mapped_column(Integer, default=14, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
