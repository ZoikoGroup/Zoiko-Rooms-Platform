from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

SUBLET_REQUEST_STATUSES = (
    "pending_verification",
    "pending_admin_review",
    # ZR-SUB-003 Section 5.1/6: the Host asked the tenant for more information
    # before deciding -- a distinct state, not a rejection or a silent stall.
    "more_information_requested",
    "approved",
    "rejected",
    # ZR-SUB-003 Section 6/13: "Withdraw pending request | Tenant" -- the
    # tenant's own action, distinct from a landlord/agent DECLINED decision.
    "withdrawn",
)

# India-scope MVP of ZR-ENG-CLR-003 Section 3's 10-type canonical taxonomy.
# ASSIGNMENT_FULL / REPLACEMENT_OCCUPANT overwrite the existing tenancy.
# SUBLEASE_PARTIAL / ADD_CO_TENANT / LODGER_OR_LICENSEE all create a real
# second, independent tenancy alongside the existing one (gated on
# Room.max_occupants) -- LODGER_OR_LICENSEE differs only in liability outcome
# (a licensee has no tenancy rights, unlike a co-tenant). ADDITIONAL_OCCUPANT
# is record-only: permission to reside without any contractual/financial
# consequence at all (ZR-ENG-CLR-003 Section 3: "Person is permitted to reside
# without becoming a contractual tenant" -- no agreement, no obligation, no
# occupancy row). The remaining types (TEMPORARY_GUEST, UNAUTHORIZED_OCCUPANCY,
# OTHER_REGULATED_TRANSFER) still aren't modeled -- rejecting them explicitly
# is the doc's own POLICY_UNCERTAIN/NOT_APPLICABLE principle: don't silently
# approve what the platform can't actually represent.
SUBLET_ARRANGEMENT_TYPES = (
    "ASSIGNMENT_FULL", "REPLACEMENT_OCCUPANT",
    "SUBLEASE_PARTIAL", "ADD_CO_TENANT", "LODGER_OR_LICENSEE",
    "ADDITIONAL_OCCUPANT",
)
# Arrangement types that replace the existing occupant vs. add a second one.
REPLACING_ARRANGEMENT_TYPES = ("ASSIGNMENT_FULL", "REPLACEMENT_OCCUPANT")
CO_TENANCY_ARRANGEMENT_TYPES = ("SUBLEASE_PARTIAL", "ADD_CO_TENANT", "LODGER_OR_LICENSEE")
# Record-only: approving these never creates an agreement, obligation, or
# occupancy -- just a permission on file.
NO_TENANCY_ARRANGEMENT_TYPES = ("ADDITIONAL_OCCUPANT",)

# Section 3's liability state model (14.4), trimmed to the states this MVP
# actually drives.
ORIGINAL_RENTER_LIABILITY_STATES = ("ACTIVE", "LIMITED", "RELEASED")
NEW_OCCUPANT_LIABILITY_STATES = ("NONE", "SUBORDINATE", "ASSIGNEE", "JOINT", "LICENSEE")


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
    # ZR-SUB-003 IMPLEMENTATION LOCK: the doc's whole point is that this decision
    # belongs to the verified landlord/Host, not Zoiko staff -- decided_by_admin_id
    # stays only as a "legal ops" override path (see crud/sublet.py), and the real,
    # expected actor for an ordinary decision is the Host, recorded here instead.
    decided_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("user_accounts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ZR-SUB-003 Section 5.1: "The tenant may respond with an additive
    # submission; the original request remains intact." One round trip is
    # enough for this MVP -- a dedicated sublet_information_request/response
    # history table is the doc's fuller model, deferred until a real need for
    # more than one round trip shows up.
    info_request_note: Mapped[str] = mapped_column(String(2000), default="")
    info_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    info_response_note: Mapped[str] = mapped_column(String(2000), default="")
    info_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ZR-SUB-003 Section 3 Step 2 Wireframe B: "Reason (optional)" -- the
    # tenant's own stated reason for the request. Distinct from
    # authority_evidence_ref (a landlord-consent-letter-type link).
    reason: Mapped[str] = mapped_column(String(2000), default="")

    # ZR-SUB-003 Section 5.2/Wireframe H: "Approval may include conditions,
    # expiry dates and scope limitations." Free-text conditions is this MVP's
    # representation of the doc's structured condition list -- approval_expires_at
    # is descriptive metadata only (no scheduler exists in this stack to enforce
    # it automatically; see services/booking_expiry.py's own admission of the
    # same gap for the sibling room-hold-expiry feature).
    approval_conditions: Mapped[str] = mapped_column(String(2000), default="")
    approval_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Deliberately separate from decided_at -- a withdrawal is the tenant's own
    # action, never a landlord/agent decision, and the doc's state table keeps
    # them as genuinely distinct events.
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    decided_by_user: Mapped["UserAccount"] = relationship()
    requested_by_guest: Mapped["Guest"] = relationship(foreign_keys=[requested_by_guest_id])
