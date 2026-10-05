"""Money a host sends back to a renter directly -- the deposit at move-out,
or a refund when a booking is cancelled before move-in. Zoiko Rooms Payment
Model: rent and deposits are paid to the host directly, so they're returned
directly too. Zoiko never holds or moves this money; it only records what
the host says they returned and whether the renter confirms receiving it
(or reports a problem)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# DEPOSIT_RETURN: deposit sent back after the tenancy ended (occupancy ENDED).
# CANCELLATION_RETURN: money sent back after a booking was cancelled before
# move-in (occupancy CANCELLED).
RENTAL_PAYMENT_RETURN_KINDS = ("DEPOSIT_RETURN", "CANCELLATION_RETURN")
# RECORDED: the host says it's sent. CONFIRMED: the renter received it.
# DISPUTED: the renter says it hasn't arrived or is wrong.
RENTAL_PAYMENT_RETURN_STATUSES = ("RECORDED", "CONFIRMED", "DISPUTED")


class RentalPaymentReturn(Base):
    __tablename__ = "rental_payment_returns"

    id: Mapped[int] = mapped_column(primary_key=True)
    occupancy_id: Mapped[int] = mapped_column(ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="RECORDED")
    tenant_guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id", ondelete="CASCADE"), nullable=False, index=True)
    recipient_party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True)
    # What was sent back, and -- for a deposit -- what was kept and why.
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    deductions_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    deductions_reason: Mapped[str] = mapped_column(String(2000), default="")
    payment_method_category: Mapped[str] = mapped_column(String(20), nullable=False)
    external_reference: Mapped[str] = mapped_column(String(255), default="")
    returned_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str] = mapped_column(String(2000), default="")
    # ZR-SUBLET-PAY-003 Section 15: the payment this return gives money back
    # on -- a separate, linked entry; the original payment is never edited.
    obligation_id: Mapped[int | None] = mapped_column(
        ForeignKey("rental_payment_obligations.id", ondelete="SET NULL"), nullable=True, index=True)
    tenant_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tenant_dispute_details: Mapped[str] = mapped_column(String(2000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    occupancy: Mapped["Occupancy"] = relationship()
