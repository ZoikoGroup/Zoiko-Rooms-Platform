from datetime import datetime

from app.schemas.common import CamelModel


class NotificationRead(CamelModel):
    id: int
    title: str
    message: str
    notification_type: str
    related_entity_type: str
    related_entity_id: str
    category: str
    priority: str
    is_read: bool
    created_at: datetime
    read_at: datetime | None


class UnreadCountRead(CamelModel):
    count: int


class NotificationPreferenceUpdate(CamelModel):
    """Section 11 gap: opted_out_categories is filtered against
    NOTIFICATION_OPTABLE_CATEGORIES server-side (crud/notification.py:
    update_preference) -- DISPUTES_AND_SAFETY can never be silenced.
    quiet_hours_start_minute/end_minute are UTC minutes-since-midnight
    (0-1439) -- see models/notification_preference.py's own docstring for
    why this build has no per-user timezone to localize against."""

    opted_out_categories: list[str] = []
    quiet_hours_enabled: bool = False
    quiet_hours_start_minute: int = 1320
    quiet_hours_end_minute: int = 420


class NotificationPreferenceRead(CamelModel):
    opted_out_categories: list[str]
    quiet_hours_enabled: bool
    quiet_hours_start_minute: int
    quiet_hours_end_minute: int
