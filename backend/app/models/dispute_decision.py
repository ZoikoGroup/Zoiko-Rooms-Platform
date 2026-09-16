from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-ENG-CLR-010 Section 19/22/26: the four concrete mechanisms this
# codebase actually has for deciding a claim's outcome -- mirrors
# TerminationDecision's own honest, build-matched basis list
# (app/models/termination_case.py) rather than inventing values nothing
# writes.
DISPUTE_DECISION_BASES = (
    "ADMIN_INTERNAL_DECISION",
    "EXTERNAL_PROCEEDING_DECIDED",
    "EXTERNAL_PROCEEDING_DISMISSED",
    "EXTERNAL_PROCEEDING_WITHDRAWN",
    "SETTLEMENT_ACCEPTED",
)
DISPUTE_DECISION_AUTHORITIES = ("admin", "external_proceeding", "settlement")

# QA-Q51/Section 25 "authority matrix": "Admin tries to mark legal
# liability using a service-level resolution code; blocked." decide_claim
# only ever fires for an A0 (Zoiko-controlled, service-level) claim
# (AC-6) -- this vocabulary is the actual enforcement mechanism: every
# value in it names a platform-service outcome, and none of them can
# express a legal/liability finding about a party (negligence, breach,
# discrimination, fault), so an admin structurally cannot "mark legal
# liability" through this field at all, rather than that being caught by
# pattern-matching free text after the fact. Only decide_claim validates a
# claim's decision against this list (record_decision/settlement
# acceptance are governed by their own, separate authorities and never
# touch it).
DISPUTE_INTERNAL_DECISION_REASON_CATEGORIES = (
    "FEE_WAIVED", "FEE_UPHELD", "POLICY_APPLIED", "EVIDENCE_INSUFFICIENT",
    "GOODWILL_CREDIT", "DUPLICATE_OR_ERROR_CORRECTED", "OTHER_SERVICE_REASON",
)


class DisputeDecision(Base):
    """ZR-ENG-CLR-010 Section 19/22/26: append-only decision history for a
    DisputeResolutionClaim, same rationale as TerminationDecision
    (app/models/termination_case.py) applied one level down. Claim.outcome/
    reason_code/decided_at/decided_by_admin_id are flattened *current-value*
    columns -- Section 26's own reopen grounds ("material new evidence,
    processing error, external decision, fraud finding") legitimately let a
    resolved claim go back to EVIDENCE and be decided again, and a
    re-decision overwrites those columns. Without this separate,
    never-updated-in-place row, the claim's PRIOR decision (who made it, on
    what basis, when) becomes silently unrecoverable the moment that
    happens -- exactly the "reproducible from canonical records without
    relying on a staff member's narrative" discipline Section 21/28 already
    require at the whole-case level (see dispute_case_export.py), now held
    at the per-decision level too.

    One row is written at every point this build actually decides a claim's
    outcome: crud/disputes.py:decide_claim (ADMIN_INTERNAL_DECISION),
    crud/dispute_external_proceeding.py:record_decision/update_status
    (EXTERNAL_PROCEEDING_*), and crud/dispute_settlement.py:respond_settlement's
    ACCEPT path (SETTLEMENT_ACCEPTED)."""

    __tablename__ = "dispute_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("dispute_resolution_claims.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    decision_basis: Mapped[str] = mapped_column(String(30), nullable=False)
    authority: Mapped[str] = mapped_column(String(20), nullable=False)
    decided_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    reason_code: Mapped[str] = mapped_column(String(50), default="")
    # QA-Q51 -- see DISPUTE_INTERNAL_DECISION_REASON_CATEGORIES above.
    # Only ever meaningfully set for decision_basis=ADMIN_INTERNAL_DECISION;
    # every other basis keeps the default (its authority is external/
    # settlement, not an admin-selected internal reason category).
    reason_category: Mapped[str] = mapped_column(String(30), default="OTHER_SERVICE_REASON")
    # Set on whichever of these two actually produced this decision -- at
    # most one is non-null (ADMIN_INTERNAL_DECISION sets neither).
    external_proceeding_id: Mapped[int | None] = mapped_column(
        ForeignKey("dispute_external_proceedings.id", ondelete="SET NULL"), nullable=True
    )
    settlement_id: Mapped[int | None] = mapped_column(ForeignKey("dispute_settlements.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    claim: Mapped["DisputeResolutionClaim"] = relationship(back_populates="decisions")
    decided_by_admin: Mapped["AdminUser"] = relationship()
