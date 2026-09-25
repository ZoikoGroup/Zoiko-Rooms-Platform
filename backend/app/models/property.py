from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

PROPERTY_STATUSES = ("active", "inactive")


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    city: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active")
    # ZR-ENG-CLR-006 Section 6: 'The Termination Policy Resolver must select
    # an effective-dated market rule set using the property jurisdiction.'
    # The single source of truth for which region's rules apply to this
    # property: market policy, market release and agreement clauses all
    # resolve from it. The API requires hosts/admins to pick an open region
    # explicitly (services/jurisdictions.py) -- this column default only
    # applies to rows created directly in code (seed data, tests).
    jurisdiction_code: Mapped[str] = mapped_column(String(10), nullable=False, default="England")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    owner_party: Mapped["Party"] = relationship(back_populates="properties")
    rooms: Mapped[list["Room"]] = relationship(back_populates="property", cascade="all, delete-orphan")
