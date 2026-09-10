"""ZR-ENG-CLR-004 Section 10.2/13.1/AC-20 'termination_record: Legal/
operational termination evidence and effective dates.' A distinct entity
from Occupancy's own flat notice_given_at/liability_end_date/
termination_effective_date/move_out_date columns (added in an earlier
round) -- those stay as a denormalized convenience for reads that only need
the current occupancy row, while this table is the authoritative,
independently-queryable termination record the spec names, created inside
crud/occupancy.py:end_occupancy alongside setting those columns."""

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

TERMINATION_BASES = ("NOTICE", "MUTUAL_SURRENDER", "BREAK_CLAUSE", "STATUTORY_GROUND", "OTHER")


class TerminationRecord(Base):
    __tablename__ = "termination_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    occupancy_id: Mapped[int] = mapped_column(ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=False)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id", ondelete="CASCADE"), nullable=False)
    basis: Mapped[str] = mapped_column(String(30), default="OTHER")
    notice_given_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    liability_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    termination_effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    physical_move_out_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    occupancy: Mapped["Occupancy"] = relationship()
    agreement: Mapped["Agreement"] = relationship()
