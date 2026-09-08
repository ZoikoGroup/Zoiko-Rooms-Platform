"""Shared login-throttling logic for both AdminUser and UserAccount -- duck-typed
over any object with failed_login_attempts (int) and locked_until (datetime|None)
columns, since the two account types don't share a base class."""
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy.orm import Session

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


class Throttleable(Protocol):
    failed_login_attempts: int
    locked_until: datetime | None


def is_locked(account: Throttleable) -> bool:
    locked_until = account.locked_until
    if locked_until is None:
        return False
    # SQLite (test suite only -- Postgres preserves this correctly) drops tzinfo
    # on round-trip even for a DateTime(timezone=True) column; treat a naive
    # value as UTC (what it was always written as) rather than crash on compare.
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    return locked_until > datetime.now(timezone.utc)


def record_failed_attempt(db: Session, account: Throttleable) -> None:
    account.failed_login_attempts += 1
    if account.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
        account.locked_until = datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)
    db.commit()


def record_successful_login(db: Session, account: Throttleable) -> None:
    if account.failed_login_attempts or account.locked_until:
        account.failed_login_attempts = 0
        account.locked_until = None
        db.commit()
