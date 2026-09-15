from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

DISPUTE_PARTY_ROLES = ("RENTER", "HOST", "REPRESENTATIVE")

DISPUTE_PARTY_REPRESENTATION_TYPES = ("SELF", "PROPERTY_MANAGER", "LEGAL_COUNSEL", "OTHER_AUTHORIZED")


class DisputeParty(Base):
    """ZR-ENG-CLR-010 Section 23 `dispute_party`: an additive audit/
    representation record -- NOT the mechanism access control runs
    through. crud/disputes.py's assert_guest_can_access_case/
    assert_party_can_access_case (Phase 1) already correctly derive case
    access from occupancy/property ownership and stay exactly as-is; this
    table exists so "who was on this case, acting in what capacity, with
    what verified authority" is queryable and auditable on its own,
    per Section 25's "Representatives require explicit authority."

    open_case (Phase 1) auto-creates one SELF row per side at case-open
    time (added_by_admin_id null -- nobody administratively added them,
    the existing ownership check in open_case is what verified them).
    crud/dispute_party.py:add_representative is the only way a
    REPRESENTATIVE row is ever created -- always admin-added, always with
    a recorded authority_evidence_ref (QA Q39: "Representative gains
    authority mid-case; access event and representation basis recorded" --
    that event is this row plus the route's log_audit_event call)."""

    __tablename__ = "dispute_parties"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    party_role: Mapped[str] = mapped_column(String(15), nullable=False)
    guest_id: Mapped[str | None] = mapped_column(ForeignKey("guests.id", ondelete="SET NULL"), nullable=True)
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id", ondelete="SET NULL"), nullable=True)
    # Only set for party_role=REPRESENTATIVE -- which side (RENTER/HOST)
    # this row acts for. Null for the two ordinary SELF rows.
    represents: Mapped[str | None] = mapped_column(String(10), nullable=True)
    representation_type: Mapped[str] = mapped_column(String(20), default="SELF")
    authority_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Free-text reference to the proof of authority -- same shape/purpose
    # as BookingChangeRequest.authority_evidence_ref (models/booking_change_request.py).
    authority_evidence_ref: Mapped[str] = mapped_column(String(1024), default="")
    communication_restrictions: Mapped[str] = mapped_column(String(500), default="")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    added_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)

    case: Mapped["DisputeResolutionCase"] = relationship()
    guest: Mapped["Guest"] = relationship()
    party: Mapped["Party"] = relationship()
    added_by_admin: Mapped["AdminUser"] = relationship()
