from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-ENG-CLR-012 Section 6's requirement taxonomy, trimmed to the two
# requirement families this MVP actually resolves and gates on.
VERIFICATION_REQUIREMENT_CODES = ("IDENTITY", "OCCUPANCY_ELIGIBILITY")

VERIFICATION_CREDENTIAL_STATUSES = ("VALID", "EXPIRED", "REVOKED")


class VerificationCredential(Base):
    """ZR-ENG-CLR-012 Section 2: 'No raw document should become a permanent
    account attribute. Verification produces a scoped credential or decision
    with provenance and validity dates.' This is that credential -- issued
    once the underlying check (IdentityVerification or
    OccupancyEligibilityCheck) passes. Downstream gates (crud/eligibility.py)
    should read THIS, not re-derive pass/fail from raw evidence rows --
    Section 24's own source-of-truth rule: 'Booking Service... must never
    infer identity or legal eligibility from the mere presence of an
    uploaded file.'

    Exactly one of source_identity_verification_id /
    source_occupancy_eligibility_check_id is set, matching requirement_code."""

    __tablename__ = "verification_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True)
    requirement_code: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="VALID")
    method: Mapped[str] = mapped_column(String(50), default="")
    jurisdiction_code: Mapped[str] = mapped_column(String(50), default="")
    # Section 2: "Policy snapshot and calculation inputs must permit
    # deterministic replay of the decision" -- which pack version resolved
    # this requirement, for audit/replay.
    policy_pack_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_identity_verification_id: Mapped[int | None] = mapped_column(ForeignKey("identity_verifications.id"), nullable=True)
    source_occupancy_eligibility_check_id: Mapped[int | None] = mapped_column(ForeignKey("occupancy_eligibility_checks.id"), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    party: Mapped["Party"] = relationship()
