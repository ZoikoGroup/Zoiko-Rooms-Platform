from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-ENG-CLR-012 Sections 10/11: Application/Affordability Evidence and
# Screening/Consumer-Report Controls share one model here -- both are
# "Host may request permitted evidence only after the Requirement Resolver
# confirms lawfulness", decided separately from the renter's core identity.
# check_type is deliberately free text, never a hardcoded enum: "No global
# criminal-record, eviction-history or credit check" -- which categories
# even exist is Host/jurisdiction configuration, not a platform taxonomy.
#
# Mirrors ZR-ENG-CLR-012 Section 24's "Screening Decision" domain:
# NOT_REQUESTED, AUTHORIZED, REPORT_PENDING, REVIEW_READY, DECIDED,
# DISPUTED_SOURCE. NOT_REQUESTED is excluded as a *stored* value for the
# same reason as every other domain's "nothing applies yet" state -- it's
# the absence of a row, not a row's own status. REPORT_PENDING and
# REVIEW_READY are collapsed into one AUTHORIZED state: this MVP has no
# live provider report-retrieval step to distinguish "waiting on the
# provider" from "ready for an admin to decide" -- both are the same
# manual-review posture until one exists. DECIDED isn't its own value
# either -- PASS/FAIL/INCONCLUSIVE already ARE the decided outcome, so a
# separate wrapper status would be redundant.
SCREENING_CHECK_STATUSES = ("AUTHORIZED", "PASS", "FAIL", "INCONCLUSIVE", "DISPUTED_SOURCE")

# AC-48 (QA pack): "User challenge corrects inaccurate evidence; credential
# is re-evaluated/versioned." DISPUTED_SOURCE (Section 24) is how that
# challenge is recorded -- not terminal, since the whole point is the
# outcome gets reconsidered, not frozen.
SCREENING_CHECK_TERMINAL_STATUSES = ("PASS", "FAIL")


class ScreeningCheck(Base):
    """Section 11: 'Zoiko must preserve the distinction between provider
    data, Host policy criteria and the final decision-maker. A provider
    score is not a legal verdict.' provider_result_summary is the raw
    signal; decision_status/decision_reason is the separate human decision
    -- never derived automatically from provider_result_summary."""

    __tablename__ = "screening_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True)
    jurisdiction_code: Mapped[str] = mapped_column(String(50), nullable=False)
    check_type: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(200), default="")
    # AC-34: "Screening/consumer-report checks require a permissible
    # purpose/authorization record where applicable" -- recorded, not
    # inferred, and required at open time (see crud validation).
    permissible_purpose: Mapped[str] = mapped_column(String(500), default="")
    host_policy_criteria: Mapped[str] = mapped_column(String(1000), default="")
    provider_result_summary: Mapped[str] = mapped_column(String(1000), default="")
    decision_status: Mapped[str] = mapped_column(String(20), default="AUTHORIZED")
    decision_reason: Mapped[str] = mapped_column(String(1000), default="")
    # AC-40/AC-47: "Every manual review records reviewer, policy version,
    # evidence, reason code and outcome" / "Every verification decision can
    # be reconstructed from policy snapshot, evidence provenance..." Which
    # MarketPolicyPack.version resolved is_screening_check_type_permitted
    # at decision time, for replay/audit.
    policy_pack_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # AC-35: "Adverse screening decisions can trigger required provider/
    # source and dispute-right communications." Set once that notice has
    # actually been sent for a FAIL decision.
    adverse_action_notice_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # AC-48: what the applicant says is wrong -- recorded at the moment the
    # dispute is raised, separate from decision_reason (the reviewer's own
    # reasoning), so both sides of the challenge stay distinguishable.
    dispute_reason: Mapped[str] = mapped_column(String(1000), default="")
    disputed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    party: Mapped["Party"] = relationship()
    reviewed_by: Mapped["AdminUser"] = relationship()
