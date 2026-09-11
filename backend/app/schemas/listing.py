from datetime import datetime

from app.schemas.common import CamelModel


class ListingBase(CamelModel):
    name: str
    property_type: str = "private_room"
    room_type: str
    # city/location: accepted here for backward compatibility, but when
    # room_id is set the server overwrites both from the room's Property
    # (address/city) -- see crud.listing._canonical_location -- so Property
    # stays the single canonical location source. latitude/longitude are
    # listing-owned with no canonical source (no geocoding provider wired up);
    # the frontend should treat them as author-supplied map pins, not derived
    # from city/location.
    city: str
    location: str
    latitude: float | None = None
    longitude: float | None = None
    price_per_night: float
    # ISO-4217-style 3-letter code, validated server-side against
    # models.listing.SUPPORTED_CURRENCIES. Defaulting to "INR" here keeps every
    # existing caller that doesn't send this field working unchanged.
    currency: str = "INR"
    guests: int
    bedrooms: int = 0
    bathrooms: int = 1
    size: int = 0
    images: list[str] = []
    amenities: list[str] = []
    tags: list[str] = []
    description: str = ""
    featured: bool = False
    room_id: int | None = None
    min_stay_nights: int = 30


class ListingCreate(ListingBase):
    contact_name: str = ""
    contact_phone: str = ""
    contact_email: str = ""


class ListingUpdate(CamelModel):
    name: str | None = None
    room_type: str | None = None
    city: str | None = None
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    price_per_night: float | None = None
    currency: str | None = None
    guests: int | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    size: int | None = None
    images: list[str] | None = None
    amenities: list[str] | None = None
    tags: list[str] | None = None
    description: str | None = None
    featured: bool | None = None
    room_id: int | None = None
    min_stay_nights: int | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None


class ListingRead(ListingBase):
    id: str
    slug: str
    rating: float
    review_count: int
    owner_id: int | None
    party_id: int | None = None
    state: str
    rejection_reason: str = ""
    suspension_reason: str = ""
    paused_at: datetime | None = None
    market_release_id: int | None = None
    contact_name: str = ""
    contact_phone: str = ""
    contact_email: str = ""
    # Real-time: PUBLISHED state alone doesn't mean a renter hasn't since
    # moved in. Computed by crud.listing.annotate_availability/is_listing_available
    # -- never trust Listing.state alone for "is this actually live" in a new caller.
    available: bool = True


class ListingRejectRequest(CamelModel):
    reason: str


class ListingSuspendRequest(CamelModel):
    reason: str


class ListingQuarantineRequest(CamelModel):
    reason: str


class ListingChangesRequestedRequest(CamelModel):
    reason: str


class PublicListingRead(ListingBase):
    """Unauthenticated, public-facing shape -- deliberately excludes the host's
    email/phone/any other private contact detail. Only the display name is
    public, matching normal marketplace attribution (e.g. "Hosted by Priya")
    rather than a way to reach the host directly."""

    id: str
    slug: str
    rating: float
    review_count: int
    owner_name: str


class PublicListingsPage(CamelModel):
    """Paginated envelope for GET /api/public/listings."""

    items: list[PublicListingRead]
    limit: int
    offset: int
    total: int
    has_more: bool
