from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

MARKET_RELEASE_STATUSES = ("draft", "active", "disabled")


class MarketRelease(Base):
    __tablename__ = "market_releases"

    id: Mapped[int] = mapped_column(primary_key=True)
    jurisdiction: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    min_stay_nights: Mapped[int] = mapped_column(Integer, default=30)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ZR-ENG-CLR-001 Section 14: per-market overrides of the policy keys
    # services/policy.py knows about (only the ones this codebase actually
    # branches on -- see that module for the full list + defaults). Empty
    # dict means "use the platform-wide default for every key" -- England
    # launch never needs this populated.
    policy_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    # ZR-ENG-CLR-004 AC-04/2.3 mode E 'Manual / unsupported: Classification
    # or form cannot be safely automated... Block self-service generation;
    # route to Admin/legal workflow.' When true,
    # services/agreement_profile.py:resolve_agreement_profile fails closed
    # for this market regardless of whether an approved clause registry
    # exists -- a legal/ops decision to route every agreement in this
    # jurisdiction to manual handling, not something a template gap alone
    # already causes.
    manual_agreement_only: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    listings: Mapped[list["Listing"]] = relationship(back_populates="market_release")
