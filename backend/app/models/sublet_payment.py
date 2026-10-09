"""ZR-SUBLET-PAY-003 -- the sublet payment arrangement.

Payments between the incoming tenant and the payee are made directly by
bank transfer, UPI or cash; Zoiko Rooms never receives, holds or forwards
sublet rent or deposits and charges no fee on them (Listing Fee only). This
record says, for one approved sublet, who is entitled to receive each
obligation and why, where the deposit goes, which direct methods are
accepted and the frozen terms. It is versioned: once locked (agreement in
force or money recorded) a change is a new version through an amendment,
never an in-place edit of history.
"""

from datetime import date, datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

RENT_PAYEE_TYPES = ("LANDLORD_AGENT", "SUBLESSOR")
DEPOSIT_ROUTES = ("NOT_REQUIRED", "PAY_TO_LANDLORD_OR_AGENT", "PAY_TO_SUBLESSOR", "PAY_TO_CUSTODIAN",
                  "PROHIBITED_OR_UNRESOLVED")
DIRECT_PAYMENT_METHODS = ("BANK_TRANSFER", "UPI", "CASH")
ARRANGEMENT_STATUSES = ("ACTIVE", "SUPERSEDED")


class SubletPaymentArrangement(Base):
    __tablename__ = "sublet_payment_arrangements"

    id: Mapped[int] = mapped_column(primary_key=True)
    sublet_request_id: Mapped[int] = mapped_column(ForeignKey("sublet_requests.id", ondelete="CASCADE"),
                                                   nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(default=1)
    previous_id: Mapped[int | None] = mapped_column(ForeignKey("sublet_payment_arrangements.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)

    rent_payee_type: Mapped[str] = mapped_column(String(20), default="LANDLORD_AGENT")
    rent_payee_party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"), nullable=True)
    # Human-readable "why this payee" basis code + the record that grants it.
    rent_payee_basis: Mapped[str] = mapped_column(String(60), default="")
    payee_authority_ref: Mapped[str] = mapped_column(String(80), default="")

    deposit_route: Mapped[str] = mapped_column(String(30), default="PROHIBITED_OR_UNRESOLVED")
    deposit_payee_party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"), nullable=True)
    custodian_name: Mapped[str] = mapped_column(String(200), default="")
    custodian_reference: Mapped[str] = mapped_column(String(200), default="")
    custodian_instructions: Mapped[str] = mapped_column(String(2000), default="")

    accepted_methods: Mapped[list] = mapped_column(JSON, default=lambda: ["BANK_TRANSFER", "UPI"])

    rent_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    deposit_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    rent_due_day: Mapped[int | None] = mapped_column(nullable=True)
    first_payment_date: Mapped[date | None] = mapped_column(nullable=True)
    deposit_pack_version: Mapped[str] = mapped_column(String(20), default="")

    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    amendment_reason: Mapped[str] = mapped_column(String(1000), default="")
    reason_codes: Mapped[list] = mapped_column(JSON, default=list)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("user_accounts.id"), nullable=True)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    sublet_request: Mapped["SubletRequest"] = relationship()  # noqa: F821
