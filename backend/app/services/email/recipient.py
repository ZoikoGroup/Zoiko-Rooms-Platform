"""The recipient's own settings, applied to every email sent to them.

- Display: dates and times in their time zone, 12- or 24-hour clock, and
  their region's date order (user_preferences, crud/user_settings.py).
- Delivery: the same notification preferences that govern in-app
  notifications (notification_preferences) also govern email:
  * a category they've opted out of isn't emailed.
  (Quiet hours no longer apply to users -- ZR-61.)
  Tier 0 messages (security, legal, safety, money actions) and account
  messages are always sent -- the same carve-out the in-app side makes.

An address with no user account (a renter invited by email, an admin) gets
the defaults and is never suppressed."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select

logger = logging.getLogger("uvicorn.error")

# Email stream -> notification category, for a message with no related record type.
_STREAM_CATEGORY = {"money": "PAYMENTS", "trust": "DISPUTES_AND_SAFETY", "security": "ACCOUNT", "organizations": "ACCOUNT"}


@dataclass
class RecipientProfile:
    time_zone: str = "UTC"
    time_format: str = "24h"
    region: str = "GB"
    opted_out_categories: list[str] = field(default_factory=list)

    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")


def lookup(email: str) -> RecipientProfile | None:
    """None when the address isn't a user account (or logging is off)."""
    from app.crud.user_settings import get_preferences
    from app.models.notification_preference import NotificationPreference
    from app.models.user_account import UserAccount
    from app.services.email import message_log

    try:
        with message_log._session() as db:
            if db is None:
                return None
            user = db.scalar(select(UserAccount).where(func.lower(UserAccount.email) == email.strip().lower()))
            if user is None:
                return None
            prefs = get_preferences(db, user)
            notif = db.scalar(select(NotificationPreference).where(NotificationPreference.recipient_key == f"user:{user.id}"))
            profile = RecipientProfile(
                time_zone=prefs.get("timeZone") or "UTC",
                time_format=prefs.get("timeFormat") or "24h",
                region=prefs.get("region") or "GB",
            )
            if notif is not None:
                profile.opted_out_categories = list(notif.opted_out_categories or [])
            return profile
    except Exception:  # noqa: BLE001 -- never let a settings lookup stop an email
        logger.exception("email: could not load recipient settings for %s", email)
        return None


def category_for(related_entity_type: str, stream_key: str) -> str | None:
    from app.models.notification import category_for_prefix

    return category_for_prefix(related_entity_type) or _STREAM_CATEGORY.get(stream_key)


def suppression_reason(profile: RecipientProfile | None, *, tier: int, stream_key: str, related_entity_type: str) -> str:
    """Empty string means send."""
    if profile is None or tier == 0:
        return ""
    category = category_for(related_entity_type, stream_key)
    if category is None or category == "ACCOUNT":
        return ""
    if category in profile.opted_out_categories:
        return f"recipient opted out of {category}"
    return ""
