"""Display formatting for email copy (ZR-COMMS-EMAIL-001 Section 4.2: exact
date, time zone and currency). The platform doesn't store a per-user time
zone yet, so times are shown explicitly in UTC rather than guessed."""

from __future__ import annotations

from datetime import date, datetime, timezone

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
    if isinstance(value, datetime):
        value = value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    return f"{value.day} {value.strftime('%B %Y')}"


def moment(value: datetime | None) -> str:
    if value is None:
        return "Not available"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return f"{value.day} {value.strftime('%B %Y')}, {value.strftime('%H:%M')} UTC"


def now() -> str:
    return moment(datetime.now(timezone.utc))


def first_name(full_name: str | None) -> str:
    return (full_name or "").strip().split(" ")[0]


def reference(prefix: str, value: int | str | None) -> str:
    if value is None or value == "":
        return "Not available"
    return f"{prefix}-{int(value):08d}" if isinstance(value, int) or str(value).isdigit() else f"{prefix}-{value}"
