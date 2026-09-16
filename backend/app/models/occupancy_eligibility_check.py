from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-ENG-CLR-012 Section 9: "Occupancy eligibility exists only where a
# jurisdiction imposes it." England's real mechanism (GOV.UK, Section 34's
# own validation example) is either an online "share code" lookup or an
# accepted manual document check -- both real, publicly documented routes.
# This MVP has no live Home Office API integration, so DIGITAL_SHARE_CODE
# still resolves to a manual admin decision (share_code is recorded for the
# record, not verified against a live government service) -- same honesty
# as every other "not really automated yet" corner of this codebase.
OCCUPANCY_ELIGIBILITY_METHODS = ("DIGITAL_SHARE_CODE", "MANUAL_DOCUMENT_CHECK")

# ZR-ENG-CLR-012 Section 8's own "Check state" table, in full -- NOT_REQUIRED
# and REQUIRED are deliberately excluded as *stored* values: they are
# resolver-computed facts (whether occupancy_eligibility_required is set for
# this jurisdiction, and whether a valid credential already exists), not
# something this row needs to represent -- a row only exists once a check
# has actually been opened, matching this MVP's on-demand (not
# every-renter-gets-a-row) design. TECHNICAL_ERROR and FRAUD_REVIEW ARE
# included even though there's no live provider: Section 16's "Manual
# Verification Operations" mode can select either as a genuine manual
# outcome (e.g. the share-code lookup service was down; evidence looks
# tampered) -- they don't require automation to be real, reachable states.
OCCUPANCY_ELIGIBILITY_STATUSES = (
    "IN_PROGRESS", "PASS", "INCONCLUSIVE", "TECHNICAL_ERROR", "FAIL_INELIGIBLE",
    "FRAUD_REVIEW", "EXPIRED", "WAIVED_POLICY", "SUSPENDED",
)

# AC-09/AC-10 (this MVP's earlier fix): only PASS/FAIL_INELIGIBLE/
# WAIVED_POLICY are genuinely terminal -- everything else "routes to
# alternate/manual review" per the doc's own next-action column, so must
# remain re-decidable rather than a dead end.
OCCUPANCY_ELIGIBILITY_TERMINAL_STATUSES = ("PASS", "FAIL_INELIGIBLE", "WAIVED_POLICY")


class OccupancyEligibilityCheck(Base):
    """One jurisdiction-required occupancy-eligibility check for one person
    (ZR-ENG-CLR-012 Section 9) -- e.g. England's right-to-rent check.
    Deliberately its own entity, never folded into IdentityVerification:
    Section 12's own doctrine is that identity, occupancy eligibility,
    screening, Host authority and property compliance are separate
    verification domains with separate purposes and retention rules."""

    __tablename__ = "occupancy_eligibility_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True)
    jurisdiction_code: Mapped[str] = mapped_column(String(50), nullable=False)
    method: Mapped[str] = mapped_column(String(30), nullable=False)
    # Only meaningful for DIGITAL_SHARE_CODE -- the code the renter shares,
    # recorded for audit even though nothing here calls a live government API.
    share_code: Mapped[str] = mapped_column(String(100), default="")
    evidence_ref: Mapped[str] = mapped_column(String(1024), default="")
    status: Mapped[str] = mapped_column(String(20), default="IN_PROGRESS")
    reason_note: Mapped[str] = mapped_column(String(1000), default="")
    checked_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Section 9: "Time-limited eligibility creates a FOLLOW_UP_REQUIRED date
    # and an automated notification/task before expiry."
    follow_up_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set the first time services/verification_followups.py notifies for this
    # check's follow_up_due_at, so the lazy sweep (no scheduler exists in
    # this stack -- see booking_expiry.py's own docstring) never re-notifies
    # the same deadline on a later pass.
    follow_up_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    party: Mapped["Party"] = relationship()
    checked_by: Mapped["AdminUser"] = relationship()
