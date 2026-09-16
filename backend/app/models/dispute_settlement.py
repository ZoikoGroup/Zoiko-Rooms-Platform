from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-ENG-CLR-010 Section 22: no persisted DRAFT -- same choice Phase 1 made
# for DisputeResolutionCase (no save-and-resume UI exists for this MVP), so
# a settlement always exists at SENT the moment it's created.
DISPUTE_SETTLEMENT_STATUSES = ("SENT", "COUNTERED", "ACCEPTED", "REJECTED", "EXPIRED", "EFFECTIVE", "VOID")

DISPUTE_SETTLEMENT_PROPOSER_ROLES = ("RENTER", "HOST")


class DisputeSettlement(Base):
    """ZR-ENG-CLR-010 Section 4/13 (P1)/22/23: a voluntary two-party
    settlement proposal. Section 13: 'Bilateral negotiation... used when
    parties can lawfully resolve and direct communication is safe' -- this
    is deliberately proposer-initiated by a renter or host, never an admin
    (Zoiko deciding a claim itself is Section 13 P3/decide_claim, a
    different path entirely).

    AC-27 ('exact terms/version hash... accepted by all required parties'):
    terms_hash is computed once at creation from terms_text/amount/
    currency/claim_ids and never recomputed -- a counter-offer is always a
    brand NEW row (see supersedes_settlement_id) with its own hash, never an
    edit of this one. Q24 'counteroffer invalidates prior acceptance
    target' falls out of this for free: the original row moves to the
    terminal COUNTERED status and can never be accepted again.

    AC-28 ('cannot silently waive non-waivable rights'): this MVP has no
    market-pack-driven waiver detector, so acknowledges_no_nonwaivable_waiver
    is an explicit, audited, required acknowledgment from the proposer
    instead -- and crud/dispute_settlement.py additionally refuses to link
    any claim whose authority_class isn't A1/A2 (Zoiko-controlled A0 and
    safety-adjacent/unresolved A6/LEGAL_REVIEW_REQUIRED claims can never be
    settled through this mechanism at all, per Section 19's own "do not use
    settlement UX to pressure waiver" rule)."""

    __tablename__ = "dispute_settlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    proposed_by_role: Mapped[str] = mapped_column(String(10), nullable=False)
    proposed_by_guest_id: Mapped[str | None] = mapped_column(ForeignKey("guests.id", ondelete="SET NULL"), nullable=True)
    proposed_by_party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(15), default="SENT")
    terms_text: Mapped[str] = mapped_column(String(2000), default="")
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    terms_hash: Mapped[str] = mapped_column(String(64), default="")
    acknowledges_no_nonwaivable_waiver: Mapped[bool] = mapped_column(Boolean, default=False)
    offered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_by_guest_id: Mapped[str | None] = mapped_column(ForeignKey("guests.id", ondelete="SET NULL"), nullable=True)
    responded_by_party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id", ondelete="SET NULL"), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_note: Mapped[str] = mapped_column(String(1000), default="")
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Section 23 `settlement.accepted_party_snapshots`: a self-contained
    # record of exactly who accepted, in what capacity, and against which
    # exact terms_hash/amount -- independent of any live join to the
    # guest/party row (which can change identity fields after the fact).
    # terms_hash itself already makes the accepted TERMS immutable
    # (AC-27); this additionally freezes the ACCEPTOR's identity/role at
    # the moment of acceptance. Empty until ACCEPT (see
    # crud/dispute_settlement.py:respond_settlement).
    accepted_party_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    supersedes_settlement_id: Mapped[int | None] = mapped_column(ForeignKey("dispute_settlements.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # AC-39 -- see app/models/dispute.py's DisputeResolutionCase.version.
    version: Mapped[int] = mapped_column(default=1)

    __mapper_args__ = {"version_id_col": version}

    case: Mapped["DisputeResolutionCase"] = relationship()
    proposed_by_guest: Mapped["Guest"] = relationship(foreign_keys=[proposed_by_guest_id])
    proposed_by_party: Mapped["Party"] = relationship(foreign_keys=[proposed_by_party_id])
    responded_by_guest: Mapped["Guest"] = relationship(foreign_keys=[responded_by_guest_id])
    responded_by_party: Mapped["Party"] = relationship(foreign_keys=[responded_by_party_id])
    claim_links: Mapped[list["DisputeSettlementClaimLink"]] = relationship(back_populates="settlement", cascade="all, delete-orphan")


class DisputeSettlementClaimLink(Base):
    """Same many-to-many shape as Phases 2/3's DisputeEvidenceClaimLink /
    DisputeExternalProceedingClaimLink -- one settlement can cover more
    than one claim on the same case."""

    __tablename__ = "dispute_settlement_claim_links"
    __table_args__ = (UniqueConstraint("settlement_id", "claim_id", name="uq_dispute_settlement_claim_links_settlement_claim"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    settlement_id: Mapped[int] = mapped_column(ForeignKey("dispute_settlements.id", ondelete="CASCADE"), nullable=False, index=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("dispute_resolution_claims.id", ondelete="CASCADE"), nullable=False, index=True)

    settlement: Mapped["DisputeSettlement"] = relationship(back_populates="claim_links")
    claim: Mapped["DisputeResolutionClaim"] = relationship()
