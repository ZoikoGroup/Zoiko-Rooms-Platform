"""Display formatting for email copy (ZR-COMMS-EMAIL-001 Section 4.2: exact
date, time zone and currency). Dates and times follow the recipient's own
settings (time zone, 12/24-hour clock, region's date order) while an email to
them is being built -- see for_recipient(); otherwise UTC, 24-hour, day first.
The time zone is always printed next to a time."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone
from typing import Iterator
from zoneinfo import ZoneInfo

# (zone, "12h"/"24h", region) for the email currently being built.
_recipient: ContextVar[tuple[ZoneInfo, str, str] | None] = ContextVar("email_recipient_display", default=None)


@contextmanager
def for_recipient(profile) -> Iterator[None]:
    token = _recipient.set((profile.zone(), profile.time_format, profile.region) if profile is not None else None)
    try:
        yield
    finally:
        _recipient.reset(token)


def _settings() -> tuple[ZoneInfo | timezone, str, str]:
    current = _recipient.get()
    return current if current is not None else (timezone.utc, "24h", "GB")


def _date_text(value: date, region: str) -> str:
    return f"{value.strftime('%B')} {value.day}, {value.year}" if region == "US" else f"{value.day} {value.strftime('%B %Y')}"

_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$", "INR": "₹"}


def money(amount: float | None, currency: str | None) -> str:
    """£399.00 (GBP) -- symbol for readability, ISO code for exactness."""
    if amount is None:
        return "Not available"
    code = (currency or "").upper()
    symbol = _SYMBOLS.get(code)
    if symbol:
        return f"{symbol}{amount:,.2f} ({code})"
    return f"{code} {amount:,.2f}".strip()


def day(value: date | datetime | None) -> str:
    if value is None:
        return "Not available"
    zone, _clock, region = _settings()
    if isinstance(value, datetime):
        value = value.astimezone(zone).date() if value.tzinfo else value.date()
    return _date_text(value, region)


def moment(value: datetime | None) -> str:
    if value is None:
        return "Not available"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    zone, clock, region = _settings()
    value = value.astimezone(zone)
    time_text = value.strftime("%I:%M %p").lstrip("0") if clock == "12h" else value.strftime("%H:%M")
    return f"{_date_text(value.date(), region)}, {time_text} {value.tzname() or 'UTC'}"


def now() -> str:
    return moment(datetime.now(timezone.utc))


def first_name(full_name: str | None) -> str:
    return (full_name or "").strip().split(" ")[0]


def reference(prefix: str, value: int | str | None) -> str:
    if value is None or value == "":
        return "Not available"
    return f"{prefix}-{int(value):08d}" if isinstance(value, int) or str(value).isdigit() else f"{prefix}-{value}"
