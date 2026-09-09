from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

SUBLET_REQUEST_STATUSES = ("pending_verification", "pending_admin_review", "approved", "rejected")

# India-scope MVP of ZR-ENG-CLR-003 Section 3's 10-type canonical taxonomy.
# ASSIGNMENT_FULL / REPLACEMENT_OCCUPANT overwrite the existing tenancy.
# SUBLEASE_PARTIAL / ADD_CO_TENANT create a real second, independent tenancy
# alongside the existing one (gated on Room.max_occupants). The remaining types
# (LODGER_OR_LICENSEE, ADDITIONAL_OCCUPANT, TEMPORARY_GUEST,
# UNAUTHORIZED_OCCUPANCY, OTHER_REGULATED_TRANSFER) still aren't modeled --
# rejecting them explicitly is the doc's own POLICY_UNCERTAIN/NOT_APPLICABLE
# principle: don't silently approve what the platform can't actually represent.
SUBLET_ARRANGEMENT_TYPES = ("ASSIGNMENT_FULL", "REPLACEMENT_OCCUPANT", "SUBLEASE_PARTIAL", "ADD_CO_TENANT")
# Arrangement types that replace the existing occupant vs. add a second one.
REPLACING_ARRANGEMENT_TYPES = ("ASSIGNMENT_FULL", "REPLACEMENT_OCCUPANT")
CO_TENANCY_ARRANGEMENT_TYPES = ("SUBLEASE_PARTIAL", "ADD_CO_TENANT")

# Section 3's liability state model (14.4), trimmed to the states this MVP
# actually drives.
ORIGINAL_RENTER_LIABILITY_STATES = ("ACTIVE", "LIMITED", "RELEASED")
NEW_OCCUPANT_LIABILITY_STATES = ("NONE", "SUBORDINATE", "ASSIGNEE", "JOINT")


class SubletRequest(Base):
    """Tracks a request from a current renter to sublet their occupancy to a new renter."""

    __tablename__ = "sublet_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Not unique -- an occupancy can have many sublet requests over its lifetime
    # (rejected, then retried; approved once, then a later co-tenant added).
    # crud/sublet.py:submit_sublet_request enforces the real rule: no two
    # PENDING requests on the same occupancy at once.
    current_occupancy_id: Mapped[int] = mapped_column(ForeignKey("occupancies.id", ondelete="CASCADE"), index=True, nullable=False)
    proposed_renter_party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending_verification", index=True)
    authority_evidence_ref: Mapped[str] = mapped_column(String(1024), default="")
    admin_decision: Mapped[str] = mapped_column(String(20), default="")
    admin_notes: Mapped[str] = mapped_column(String(2000), default="")
    decided_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    arrangement_type: Mapped[str] = mapped_column(String(30), default="ASSIGNMENT_FULL")
    # Captured once, at submission -- the occupancy's own guest_id is overwritten
    # on approval, so it can no longer answer "who actually requested this?" after
    # the fact. This column is what makes that question answerable in history.
    requested_by_guest_id: Mapped[str | None] = mapped_column(ForeignKey("guests.id", ondelete="SET NULL"), nullable=True, index=True)
    original_renter_liability: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    new_occupant_liability: Mapped[str] = mapped_column(String(20), default="NONE")
    # Explicit, auditable record that the deposit was NOT touched by this sublet
    # (ZR-ENG-CLR-003 Rule 4.6 / AC-08) -- rather than "nothing happened to it"
    # being an implicit absence of code, it's a recorded decision.
    deposit_disposition: Mapped[str] = mapped_column(String(40), default="")
    policy_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    # ZR-ENG-CLR-003 Rule 4.5: resolved from the market pack per arrangement
    # type, not hard-coded to "Host" or "original renter".
    payee_model: Mapped[str] = mapped_column(String(30), default="")
    # Only set for SUBLEASE_PARTIAL/ADD_CO_TENANT -- the new, independent signed
    # agreement (own rent + deposit obligations) created for the co-tenant. They
    # pay against and move in on this exactly like any other agreement; the
    # occupancy itself only exists after that, same as every other tenancy.
    new_agreement_id: Mapped[int | None] = mapped_column(ForeignKey("agreements.id", ondelete="SET NULL"), nullable=True)

    current_occupancy: Mapped["Occupancy"] = relationship(foreign_keys=[current_occupancy_id])
    new_agreement: Mapped["Agreement"] = relationship(foreign_keys=[new_agreement_id])
    proposed_renter_party: Mapped["Party"] = relationship()
    decided_by_admin: Mapped["AdminUser"] = relationship()
    requested_by_guest: Mapped["Guest"] = relationship(foreign_keys=[requested_by_guest_id])
