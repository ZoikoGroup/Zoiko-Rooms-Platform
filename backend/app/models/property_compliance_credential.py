from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-ENG-CLR-012 Section 14: "Property compliance should be modeled as
# structured credentials, not attachment folders." requirement_code is
# deliberately free text set by policy configuration
# (MarketPolicyPack.required_property_compliance_codes), never a hardcoded
# enum here -- which certificate/registration classes exist is per-jurisdiction
# law, not a platform-wide taxonomy (Section 1's "no hard-coded country
# branches" rule, applied to property compliance).
#
# ZR-ENG-CLR-012 Section 14's own "Credential state" table. NOT_APPLICABLE
# and REQUIRED are deliberately excluded as *stored* values -- like
# OccupancyEligibilityCheck's NOT_REQUIRED/REQUIRED, they're resolver-
# computed facts (does required_property_compliance_codes even name this
# code; does no VALID row exist yet) rather than something a credential row
# needs to represent; a row only exists once a Host or admin has actually
# started one. EXPIRING is also excluded as a stored value -- see
# to_property_compliance_credential_read, which computes it from VALID +
# expires_at rather than storing a second, potentially-stale copy of the
# same fact (Section 24's source-of-truth rule).
#
# AC-43: "Property credential UI distinguishes VALID from DECLARED/
# UNVERIFIED." UNDER_REVIEW is the doc's own name for that Host self-
# declaration state (evidence submitted, not yet confirmed) -- it
# deliberately never satisfies get_valid_property_compliance_credential
# (VALID only), so an UNDER_REVIEW row can never silently pass the
# jurisdiction_gates_pass gate.
PROPERTY_COMPLIANCE_CREDENTIAL_STATUSES = ("UNDER_REVIEW", "VALID", "EXPIRED", "REVOKED", "SUSPENDED")


class PropertyComplianceCredential(Base):
    __tablename__ = "property_compliance_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    requirement_code: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="VALID")
    issuer_source: Mapped[str] = mapped_column(String(200), default="")
    evidence_ref: Mapped[str] = mapped_column(String(1024), default="")
    method: Mapped[str] = mapped_column(String(50), default="")
    jurisdiction_code: Mapped[str] = mapped_column(String(50), default="")
    # AC-40/AC-47: which MarketPolicyPack.version was in effect for this
    # jurisdiction at issuance, for replay/audit -- best-effort (null if no
    # pack existed for this jurisdiction at the time), same fail-open
    # posture as every other resolver call in this domain.
    policy_pack_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issued_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ZR-ENG-CLR-012 AC-31: idempotency marker for
    # services/verification_followups.py's expiry-reminder sweep -- same
    # pattern as OccupancyEligibilityCheck.follow_up_notified_at.
    expiry_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str] = mapped_column(String(500), default="")
    # Section 14: "SUSPENDED || Temporary hold pending investigation. ||
    # Quarantine affected listing/feature." Deliberately separate from
    # revoked_at/revoked_reason -- unlike a revocation, a suspension is
    # meant to be resumed (see resume_suspended_property_compliance_
    # credential), so it needs its own history rather than overloading the
    # "why is this no longer VALID, permanently" fields.
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    room: Mapped["Room"] = relationship()
    issued_by: Mapped["AdminUser"] = relationship()
