from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-ENG-CLR-010 Section 26: the deadline types this MVP actually produces
# or lets an admin set. TRIAGE/EVIDENCE_CLOSE auto-creation is deliberately
# not built -- see app/services/dispute_operational_metrics.py's own
# docstring on why this MVP's synchronous open_case has no real "triage
# happened later" moment to anchor an automatic timer to. PARTY_RESPONSE/
# EVIDENCE_CLOSE stay admin-set-on-demand (Section 26: "forum pack or case
# officer sets deadline with extension grounds").
DISPUTE_DEADLINE_TYPES = ("PARTY_RESPONSE", "EVIDENCE_CLOSE", "INTERNAL_REVIEW")

# "Breached" is deliberately NOT a stored status -- it's a computed fact
# (due_at < now() and status == "PENDING", see crud/dispute_deadline.py:
# is_overdue) that a nonexistent scheduler would otherwise have to flip.
DISPUTE_DEADLINE_STATUSES = ("PENDING", "MET", "EXTENDED", "CANCELLED")

DISPUTE_DEADLINE_SOURCES = ("SYSTEM_DEFAULT", "ADMIN_SET")


class DisputeDeadline(Base):
    """ZR-ENG-CLR-010 Section 23 `case_deadline` / Section 26 clocks. One
    row per tracked deadline -- a case-level one (claim_id null) or a
    claim-level one (e.g. the INTERNAL_REVIEW window crud/disputes.py:
    decide_claim creates automatically for every A0 decision).

    AC-33 "Party evidence response due date is extended; original deadline
    and extension basis remain auditable": original_due_at is set once, on
    the FIRST extension only (crud/dispute_deadline.py:extend_deadline),
    and never overwritten again -- due_at always holds the current
    (possibly-extended) deadline, original_due_at always holds what it was
    before any extension ever happened."""

    __tablename__ = "dispute_deadlines"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("dispute_resolution_claims.id", ondelete="CASCADE"), nullable=True, index=True)
    deadline_type: Mapped[str] = mapped_column(String(20), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    original_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # AC-29 "Deadlines are computed from the relevant forum pack, with
    # reminders and extension audit." No notification-sending scheduler
    # exists anywhere in this codebase (same limitation this module's own
    # docstring already states for "breached") -- reminder_at is computed
    # once at creation (crud/dispute_deadline.py:_compute_reminder_at, the
    # midpoint of the response window, a reasonable MVP default) and
    # exposed via is_reminder_due() for a caller (a future digest job, an
    # admin dashboard query) to act on; nothing here sends anything itself.
    reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(15), default="PENDING")
    extension_basis: Mapped[str] = mapped_column(String(1000), default="")
    source: Mapped[str] = mapped_column(String(20), default="ADMIN_SET")
    created_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    case: Mapped["DisputeResolutionCase"] = relationship()
    claim: Mapped["DisputeResolutionClaim"] = relationship()
    created_by_admin: Mapped["AdminUser"] = relationship()
