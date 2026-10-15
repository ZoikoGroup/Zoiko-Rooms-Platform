"""The recipient's own settings, applied to every email sent to them.

- Display: dates and times in their time zone, 12- or 24-hour clock, and
  their region's date order (user_preferences, crud/user_settings.py).
- Delivery: the same notification preferences that govern in-app
  notifications (notification_preferences) also govern email:
  * a category they've opted out of isn't emailed;
  * during their quiet hours (in their own time zone) advisory emails
    (tier 2+) aren't sent.
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
    quiet_hours_enabled: bool = False
    quiet_hours_start_minute: int = 1320
    quiet_hours_end_minute: int = 420

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
                profile.quiet_hours_enabled = bool(notif.quiet_hours_enabled)
                profile.quiet_hours_start_minute = notif.quiet_hours_start_minute
                profile.quiet_hours_end_minute = notif.quiet_hours_end_minute
            return profile
    except Exception:  # noqa: BLE001 -- never let a settings lookup stop an email
        logger.exception("email: could not load recipient settings for %s", email)
        return None


def category_for(related_entity_type: str, stream_key: str) -> str | None:
    from app.models.notification import _CATEGORY_BY_TYPE_PREFIX

    kind = (related_entity_type or "").strip()
    if kind in _CATEGORY_BY_TYPE_PREFIX:
        return _CATEGORY_BY_TYPE_PREFIX[kind]
    head = kind.split("_", 1)[0]
    if head in _CATEGORY_BY_TYPE_PREFIX:
        return _CATEGORY_BY_TYPE_PREFIX[head]
    return _STREAM_CATEGORY.get(stream_key)


def in_quiet_hours(profile: RecipientProfile, now: datetime | None = None) -> bool:
    if not profile.quiet_hours_enabled:
        return False
    local = (now or datetime.now(timezone.utc)).astimezone(profile.zone())
    minute = local.hour * 60 + local.minute
    start, end = profile.quiet_hours_start_minute, profile.quiet_hours_end_minute
    return (start <= minute < end) if start <= end else (minute >= start or minute < end)


def suppression_reason(profile: RecipientProfile | None, *, tier: int, stream_key: str, related_entity_type: str) -> str:
    """Empty string means send."""
    if profile is None or tier == 0:
        return ""
    category = category_for(related_entity_type, stream_key)
    if category is None or category == "ACCOUNT":
        return ""
    if category in profile.opted_out_categories:
        return f"recipient opted out of {category}"
    if tier >= 2 and in_quiet_hours(profile):
        return "recipient's quiet hours"
    return ""
