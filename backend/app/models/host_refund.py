from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# DUE -> MARKED_PAID (host) -> CONFIRMED (renter, or deemed after the
# confirm window). DUE past due_by -> OVERDUE. The renter can say a marked
# payment never arrived -> NOT_RECEIVED (goes to the disputes flow).
HOST_REFUND_STATUSES = ("DUE", "MARKED_PAID", "CONFIRMED", "OVERDUE", "NOT_RECEIVED")


class HostRefund(Base):
    """Rent the renter paid straight to the host for days after the tenancy
    ended, which the host owes back. Rent is paid to hosts directly, so the
    platform can't refund it itself: the host pays and marks it paid, the
    renter confirms receipt (silence for the confirm window counts as
    confirmation). Created automatically when a tenancy ends
    (crud/tenancy_settlement.py) -- no admin approval."""

    __tablename__ = "host_refunds"

    id: Mapped[int] = mapped_column(primary_key=True)
    occupancy_id: Mapped[int] = mapped_column(ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=False, index=True)
    termination_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("termination_cases.id", ondelete="SET NULL"), nullable=True,
    )
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), default="")
    due_by: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="DUE", index=True)
    payment_reference: Mapped[str] = mapped_column(String(200), default="")
    marked_paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deemed_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    not_received_note: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    occupancy: Mapped["Occupancy"] = relationship()
