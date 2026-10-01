from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Lister, Property & Authority Verification wireframe: a claim distinct from
# both IdentityVerification (who the lister is) and PropertyComplianceCredential
# (per-jurisdiction regulatory documents, e.g. gas safety/EPC/HMO license) --
# this is "is the property/address itself real and evidenced." Mirrors
# AuthorityRecord's own status vocabulary/shape (see models/authority_record.py)
# rather than inventing a new one.
PROPERTY_VERIFICATION_STATUSES = (
    "pending", "verified", "rejected", "additional_evidence_required", "revoked",
)


class PropertyVerification(Base):
    __tablename__ = "property_verifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(1024), default="")

    # The actual uploaded evidence document (core/property_verification_uploads.py)
    # -- evidence_ref above was, until now, the only thing ever recorded: a
    # free-text description with no real file behind it. Same
    # never-publicly-mounted-directory convention as
    # IdentityVerification.document_file_path; never expose this path
    # directly, only through an authenticated download route.
    document_file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    document_file_original_name: Mapped[str] = mapped_column(String(255), default="")
    document_file_content_type: Mapped[str] = mapped_column(String(100), default="")
    document_file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # services/document_ocr.py:check_identity_details_in_document, via
    # crud/property_verification.py:_run_ocr_identity_cross_check --
    # checks this one document for EITHER the owner's registered name OR
    # their identity document's already-stored number (reused, never
    # re-OCR'd); either alone is enough, neither is mandatory. Two earlier,
    # more complex versions (address/city/landmark matching, then
    # requiring the name on both documents) were each simplified away on
    # explicit instruction, so ocr_address_matched below is legacy (kept
    # for existing rows, no longer written to). ocr_extracted_text is a
    # short snippet kept for admin review, never the full document text.
    ocr_extracted_text: Mapped[str] = mapped_column(String(500), default="")
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ocr_address_matched: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ocr_name_matched: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Regex-only (no OCR) details grabbed from the upload -- the PDF text
    # layer plus the typed evidence_ref (services/document_regex.py) -- and
    # how they compare against the host's name and this room's property
    # address. None = nothing readable to compare (e.g. a photo).
    extracted_owner_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    extracted_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    extracted_document_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name_matched: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    address_matched: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Map check of the property's own address (services/geocoding.py), taken
    # at submission. Auto-verification requires geocode_status == "FOUND":
    # the address resolved at street/house level in the region's country.
    # None = never checked (rows submitted before this existed).
    geocode_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    geocode_provider: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default="")
    geocode_query: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    geocode_formatted_address: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    geocode_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    geocode_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    geocode_precision: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default="")
    geocode_country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="", server_default="")
    geocode_detail: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    geocoded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # sha256 of the uploaded file, so the same document reused for another
    # host's room is caught and sent to a reviewer instead of auto-verified.
    document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(30), default="pending")
    verifier_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    verifier_notes: Mapped[str] = mapped_column(String(1000), default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    party: Mapped["Party"] = relationship()
    room: Mapped["Room"] = relationship()
    verifier_admin: Mapped["AdminUser"] = relationship()

    @property
    def has_document(self) -> bool:
        return bool(self.document_file_path)

    @property
    def google_maps_url(self) -> str:
        """Public Google Maps link to where the address resolved (or a search
        for the address text if it didn't), for hosts and reviewers."""
        from app.services.geocoding import google_maps_url

        return google_maps_url(self.geocode_latitude, self.geocode_longitude, self.geocode_query)
