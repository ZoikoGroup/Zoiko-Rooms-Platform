from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Private-room-only per the marketplace standard. Kept as a column (rather than dropped)
# because bookings/reviews/analytics/search -- explicitly out of scope for this pass --
# still read it; new listings are constrained to "private_room" at the API layer.
PROPERTY_TYPES = ("private_room",)

LISTING_STATES = (
    "DRAFT", "EVIDENCE_PENDING", "REVIEW", "REJECTED", "APPROVED",
    "PUBLISHED", "PAUSED", "SUSPENDED", "WITHDRAWN", "ARCHIVED",
)

# Explicitly stored per listing rather than derived from country/market -- that
# derivation is future work (see the currency architecture audit). Not an
# exhaustive ISO 4217 list, just the markets named in that audit; widening this
# tuple later is additive and safe.
SUPPORTED_CURRENCIES = ("INR", "GBP", "USD", "EUR", "CAD", "AUD", "AED", "SGD", "NZD")

# No stated limit existed before this; 10 is a reasonable cap on top of the
# per-file size limit (settings.max_upload_size_mb) so a listing can't grow an
# unbounded images array.
MAX_LISTING_IMAGES = 10


class Listing(Base):
    __tablename__ = "listings"
    # 0001_initial.py created a table-level UNIQUE constraint on slug plus a
    # separately named plain index (not a combined unique index) -- declared
    # explicitly here, matching the two real objects in the database, so
    # `alembic check` doesn't report a phantom constraint-vs-index diff.
    __table_args__ = (
        UniqueConstraint("slug", name="listings_slug_key"),
        Index("ix_listings_slug", "slug"),
    )

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    property_type: Mapped[str] = mapped_column(String(20), default="private_room")
    room_type: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str] = mapped_column(String(500), nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_per_night: Mapped[float] = mapped_column(Float, nullable=False)
    # Still Float, still just price_per_night's currency -- widening this to Numeric
    # and deriving it from market/jurisdiction are both separate, later tasks.
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    rating: Mapped[float] = mapped_column(Float, default=4.5)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    guests: Mapped[int] = mapped_column(Integer, nullable=False)
    bedrooms: Mapped[int] = mapped_column(Integer, default=0)
    bathrooms: Mapped[int] = mapped_column(Integer, default=1)
    size: Mapped[int] = mapped_column(Integer, default=0)
    images: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    amenities: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    description: Mapped[str] = mapped_column(String(2000), default="")
    featured: Mapped[bool] = mapped_column(Boolean, default=False)
    # Admin-created listings retain their admin owner. USER-hosted listings are
    # owned by their Party and deliberately do not require an AdminUser account.
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    party_id: Mapped[int | None] = mapped_column(
        ForeignKey("parties.id", ondelete="CASCADE"), nullable=True, index=True
    )

    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id"), nullable=True, index=True)
    market_release_id: Mapped[int | None] = mapped_column(ForeignKey("market_releases.id"), nullable=True)
    min_stay_nights: Mapped[int] = mapped_column(Integer, default=30)
    state: Mapped[str] = mapped_column(String(20), default="DRAFT")
    # Set once, the first time a listing reaches PUBLISHED (see publish_listing) --
    # never touched by a later pause/republish. Exists so alert-matching can ask
    # "what's newly published since I last checked", not just "what's live now".
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set by an admin/super admin when moving REVIEW -> REJECTED; cleared again on
    # resubmission. Empty for every other state.
    rejection_reason: Mapped[str] = mapped_column(String(1000), default="")

    # Optional per-listing override of the owner account's contact details --
    # left blank, the public API falls back to the owner's name/email/phone.
    contact_name: Mapped[str] = mapped_column(String(255), default="")
    contact_phone: Mapped[str] = mapped_column(String(50), default="")
    contact_email: Mapped[str] = mapped_column(String(255), default="")

    owner: Mapped["AdminUser"] = relationship(back_populates="listings")
    party: Mapped["Party"] = relationship(back_populates="listings")
    room: Mapped["Room"] = relationship(back_populates="listings")
    market_release: Mapped["MarketRelease"] = relationship(back_populates="listings")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="listing")
    reviews: Mapped[list["Review"]] = relationship(back_populates="listing")
