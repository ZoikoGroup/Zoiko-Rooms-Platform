from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PublicRateLimit(Base):
    """Shared, DB-backed rate-limit bucket for anonymous public endpoints.

    One row per unique bucket key (hash of caller identity + fixed time window).
    Counters are incremented atomically via an INSERT ... ON CONFLICT ... DO
    UPDATE UPSERT so a multi-worker deployment enforces the same budget as a
    single process (in-memory limiters let one visitor use workers x budget).
    """

    __tablename__ = "public_rate_limits"

    bucket_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    window_start: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )