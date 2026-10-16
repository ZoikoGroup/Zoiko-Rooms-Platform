import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud.user import update_user_password
from app.models.admin_user import AdminUser
from app.models.password_reset_token import PasswordResetToken
from app.models.user_account import UserAccount


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_reset_token(db: Session, user: UserAccount) -> str:
    """Creates a single-use, time-limited reset token. Returns the raw token --
    only its SHA-256 hash is persisted, mirroring how passwords themselves are
    only ever stored hashed."""
    raw_token = secrets.token_urlsafe(32)
    record = PasswordResetToken(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.password_reset_token_expire_minutes),
    )
    db.add(record)
    db.commit()
    return raw_token


def _get_valid_token_record(db: Session, raw_token: str) -> PasswordResetToken | None:
    record = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == _hash_token(raw_token)))
    if not record or record.used_at is not None:
        return None
    expires = record.expires_at if record.expires_at.tzinfo else record.expires_at.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        return None
    return record


def reset_password_with_token(db: Session, raw_token: str, new_password: str) -> bool:
    """Consumes a reset token and sets the new password. Returns False for any
    invalid/expired/already-used token without distinguishing why, so the
    reset-password endpoint's error response stays generic too."""
    record = _get_valid_token_record(db, raw_token)
    if not record or record.user_id is None:
        return False

    user = db.get(UserAccount, record.user_id)
    if not user or not user.is_active:
        return False

    update_user_password(db, user, new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    record.used_at = datetime.now(timezone.utc)
    db.commit()

    # ZR-IDENTITY-001 Section 7.3: account recovery can require identity
    # re-verification where the country pack says so.
    from app.services.identity.service import on_account_recovery

    on_account_recovery(db, user)
    return True


# --- Admin accounts ----------------------------------------------------------


def create_admin_reset_token(db: Session, admin: AdminUser) -> str:
    raw_token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        admin_user_id=admin.id,
        token_hash=_hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.password_reset_token_expire_minutes),
    ))
    db.commit()
    return raw_token


def reset_admin_password_with_token(db: Session, raw_token: str, new_password: str) -> bool:
    """Same single-use, generic-failure rules as the user reset. Every admin
    session issued before the reset stops working (see get_current_admin)."""
    from app.crud.admin import update_password

    record = _get_valid_token_record(db, raw_token)
    if not record or record.admin_user_id is None:
        return False
    admin = db.get(AdminUser, record.admin_user_id)
    if not admin or not admin.is_active:
        return False
    record.used_at = datetime.now(timezone.utc)
    admin.password_changed_at = datetime.now(timezone.utc)
    update_password(db, admin, new_password)
    return True
