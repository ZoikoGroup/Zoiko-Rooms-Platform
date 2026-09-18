from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Fail-closed: a room can never publish while classification is UNKNOWN or UNSUPPORTED.
REVIEW_STATES = ("UNKNOWN", "UNSUPPORTED", "APPROVED")


class OccupancyClassification(Base):
    __tablename__ = "occupancy_classifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), unique=True, nullable=False)
    classification: Mapped[str] = mapped_column(String(100), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_ref: Mapped[str] = mapped_column(String(1024), default="")
    jurisdiction: Mapped[str] = mapped_column(String(50), default="England")
    rule_version: Mapped[int] = mapped_column(Integer, default=1)
    review_state: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    room: Mapped["Room"] = relationship(back_populates="occupancy_classification")
