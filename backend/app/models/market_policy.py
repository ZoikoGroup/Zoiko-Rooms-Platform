from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# ZR-ENG-CLR-002 Section 13.2 / ZR-ENG-CLR-003 Section 13.2's "confidence" field --
# this is the honest signal that VERIFIED means "checked against real regulator
# guidance", not just "someone typed a number in". Every row this platform ships
# with today is REVIEW_REQUIRED: reasonable placeholder values, not a substitute
# for actual legal review before any of this governs real money or real tenancies.
MARKET_POLICY_CONFIDENCE_LEVELS = ("VERIFIED", "REVIEW_REQUIRED", "DEPRECATED", "EMERGENCY_BLOCK")

DEPOSIT_CUSTODY_MODELS = ("STATUTORY_SCHEME", "GOVERNMENT_BOND", "REGULATED_ESCROW", "TRUST_ACCOUNT", "HOST_OR_AGENT", "OTHER_APPROVED")
CONSENT_STANDARDS = ("HOST_ABSOLUTE_DISCRETION", "REASONABLE_REFUSAL_ONLY", "NOTICE_ONLY", "STATUTORY_RESPONSE_DEADLINE")
PAYEE_MODELS = ("ORIGINAL_RENTER_PAYEE", "HOST_OR_LANDLORD_PAYEE", "AUTHORIZED_AGENT_PAYEE", "SPLIT_PAYEE", "EXTERNAL_PAYEE_RECORDED")


class MarketPolicyPack(Base):
    """One jurisdiction's resolved rule set for deposits and subletting, versioned
    and effective-dated (ZR-ENG-CLR-002 Section 3.2 / ZR-ENG-CLR-003 Section 13.2).
    Deposit/sublet code must resolve values from here -- never branch on a
    jurisdiction_code string directly (both docs' explicit "no hard-coded country
    branches" rule)."""

    __tablename__ = "market_policy_packs"

    id: Mapped[int] = mapped_column(primary_key=True)
    jurisdiction_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    confidence: Mapped[str] = mapped_column(String(20), default="REVIEW_REQUIRED")
    legal_source_note: Mapped[str] = mapped_column(String(2000), default="")

    # -- Deposit policy (Section 2) --
    deposit_instrument_allowed: Mapped[str] = mapped_column(String(20), default="OPTIONAL")
    deposit_max_rent_multiple: Mapped[float] = mapped_column(Numeric(6, 2), default=3.0)
    deposit_custody_model: Mapped[str] = mapped_column(String(30), default="HOST_OR_AGENT")
    deposit_protection_deadline_days: Mapped[int | None] = mapped_column(nullable=True)
    deposit_release_deadline_days: Mapped[int] = mapped_column(default=30)

    # -- Sublet policy (Section 3) --
    sublet_consent_standard: Mapped[str] = mapped_column(String(30), default="STATUTORY_RESPONSE_DEADLINE")
    sublet_consent_response_days: Mapped[int] = mapped_column(default=14)
    sublet_max_rent_multiple_of_original: Mapped[float] = mapped_column(Numeric(6, 2), default=1.0)
    sublet_assignment_payee_model: Mapped[str] = mapped_column(String(30), default="HOST_OR_LANDLORD_PAYEE")
    sublet_sublease_payee_model: Mapped[str] = mapped_column(String(30), default="ORIGINAL_RENTER_PAYEE")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
