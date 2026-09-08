from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.login_throttle import LOCKOUT_MINUTES, is_locked, record_failed_attempt, record_successful_login
from app.core.security import hash_password, verify_password
from app.models.party import Party
from app.models.user_account import UserAccount


def get_user_by_email(db: Session, email: str) -> UserAccount | None:
    return db.scalar(select(UserAccount).where(UserAccount.email == email))


def get_user_by_id(db: Session, user_id: int) -> UserAccount | None:
    return db.get(UserAccount, user_id)


def get_user_by_party_id(db: Session, party_id: int | None) -> UserAccount | None:
    """None for a Party that has no linked UserAccount at all -- e.g. the internal
    operator party an admin gets auto-provisioned when they create a property
    directly, which nobody registers a personal account for."""
    if party_id is None:
        return None
    return db.scalar(select(UserAccount).where(UserAccount.party_id == party_id))


def authenticate_user(db: Session, email: str, password: str) -> UserAccount | None:
    user = get_user_by_email(db, email)
    if not user:
        return None
    if is_locked(user):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many failed login attempts. Try again in {LOCKOUT_MINUTES} minutes.",
        )
    if not verify_password(password, user.hashed_password):
        record_failed_attempt(db, user)
        return None
    record_successful_login(db, user)
    return user


def create_user(db: Session, email: str, password: str, full_name: str, phone: str = "") -> UserAccount:
    """Create a new user and auto-provision a renter Party."""
    # Check if user already exists
    if get_user_by_email(db, email):
        raise ValueError("User with this email already exists")

    # Create user account
    user = UserAccount(
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name,
        phone=phone,
        is_active=True,
        email_verified=False,
    )

    # Create default renter party for this user
    party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()

    user.party_id = party.id
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user_profile(db: Session, user: UserAccount, full_name: str, phone: str = "") -> UserAccount:
    user.full_name = full_name
    user.phone = phone
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def update_user_password(db: Session, user: UserAccount, new_password: str) -> None:
    user.hashed_password = hash_password(new_password)
    user.updated_at = datetime.now(timezone.utc)
    db.commit()


def mark_email_verified(db: Session, user: UserAccount) -> UserAccount:
    user.email_verified = True
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def deactivate_user(db: Session, user: UserAccount) -> UserAccount:
    user.is_active = False
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user
