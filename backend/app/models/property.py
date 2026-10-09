from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

PROPERTY_STATUSES = ("active", "inactive")


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    city: Mapped[str] = mapped_column(String(255), nullable=False)
    # Optional -- crud/property_verification.py's OCR check uses this as a
    # fallback signal only, when the formal address+owner-name match fails.
    # A nearby landmark ("near XYZ Mall") isn't itself a real proof-of-
    # address signal (see services/document_ocr.py's own docstring on why
    # it's never the primary check), but some genuine local documents do
    # print it alongside a less-formal address -- worth trying before
    # falling back to a human, never instead of the real check.
    landmark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    # ZR-ENG-CLR-006 Section 6: 'The Termination Policy Resolver must select
    # an effective-dated market rule set using the property jurisdiction.'
    # The single source of truth for which region's rules apply to this
    # property: market policy, market release and agreement clauses all
    # resolve from it. The API requires hosts/admins to pick an open region
    # explicitly (services/jurisdictions.py) -- this column default only
    # applies to rows created directly in code (seed data, tests).
    jurisdiction_code: Mapped[str] = mapped_column(String(10), nullable=False, default="England")

    # ZR-PROPERTY-VERIFY-001 Section 12.1 canonical structured address.
    # `address` / `city` above stay as the display mirror (line 1 [+ line 2]
    # / locality) that the rest of the app reads.
    address_line_1: Mapped[str] = mapped_column(String(300), default="", server_default="")
    address_line_2: Mapped[str] = mapped_column(String(300), default="", server_default="")
    subpremise: Mapped[str] = mapped_column(String(50), default="", server_default="")  # unit / flat / apt
    locality: Mapped[str] = mapped_column(String(200), default="", server_default="")
    administrative_area: Mapped[str] = mapped_column(String(200), default="", server_default="")
    postal_code: Mapped[str] = mapped_column(String(20), default="", server_default="")
    country_code: Mapped[str] = mapped_column(String(2), default="", server_default="")  # ISO 3166-1 alpha-2
    canonical_formatted_address: Mapped[str] = mapped_column(String(600), default="", server_default="")
    # Section 20 transliteration: the address as the host wrote it (local
    # script), kept when it differs from the provider-normalized form.
    address_local: Mapped[str] = mapped_column(String(600), default="", server_default="")
    property_kind: Mapped[str] = mapped_column(String(12), default="", server_default="")
    building_name: Mapped[str] = mapped_column(String(200), default="", server_default="")
    floor: Mapped[str] = mapped_column(String(20), default="", server_default="")
    # Private location (Section 10): never served on public endpoints.
    latitude_private: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude_private: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_precision: Mapped[str] = mapped_column(String(14), default="", server_default="")
    public_location_decimals: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
    geocode_status: Mapped[str] = mapped_column(String(12), default="", server_default="")
    pin_status: Mapped[str] = mapped_column(String(16), default="", server_default="")
    provider_refs: Mapped[list] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    # Optimistic concurrency for address/location updates (Section 13.3).
    location_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    owner_party: Mapped["Party"] = relationship(back_populates="properties")
    rooms: Mapped[list["Room"]] = relationship(back_populates="property", cascade="all, delete-orphan")
