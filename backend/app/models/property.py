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
    # Defaults to "England" -- the only jurisdiction this build actually has
    # a real market-pack/agreement-clause registry for (see
    # services/agreement_profile.py:SUPPORTED_JURISDICTION). This platform
    # targets foreign markets, not India -- "IN" is not a supported
    # jurisdiction and must never be the silent default.
    jurisdiction_code: Mapped[str] = mapped_column(String(10), nullable=False, default="England")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    owner_party: Mapped["Party"] = relationship(back_populates="properties")
    rooms: Mapped[list["Room"]] = relationship(back_populates="property", cascade="all, delete-orphan")
