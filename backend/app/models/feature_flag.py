"""Feature-flag runtime override (Phase 7, ZR-AI-DEVOPS / FRS §2).

Server-authoritative, audit-logged overrides for capability gating. The
authoritative source of truth is the registry default in
``app.services.feature_flags``; this table only records *overrides* that deviate
from the safe default (e.g. an operations kill switch or a staged rollout).

Only allow-listed, non-secret flag names are writable (validated by the service,
never trusting this table's contents). Changes are audit-logged (``flag.*``).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FeatureFlag(Base):
    """An override for a single allow-listed feature flag."""

    __tablename__ = "feature_flags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    value: Mapped[bool] = mapped_column(Boolean, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled_by: Mapped[str] = mapped_column(String(120), nullable=False, default="system")
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
