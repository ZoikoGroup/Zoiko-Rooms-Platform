from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.admin_user import AdminSettings, AdminUser


def get_admin_by_email(db: Session, email: str) -> AdminUser | None:
    return db.scalar(select(AdminUser).where(AdminUser.email == email))


def get_admin_by_id(db: Session, admin_id: int) -> AdminUser | None:
    return db.get(AdminUser, admin_id)


def authenticate(db: Session, email: str, password: str) -> AdminUser | None:
    admin = get_admin_by_email(db, email)
    if not admin or not verify_password(password, admin.hashed_password):
        return None
    return admin


def create_admin(db: Session, email: str, password: str, full_name: str = "Zoiko Admin") -> AdminUser:
    admin = AdminUser(email=email, hashed_password=hash_password(password), full_name=full_name)
    admin.settings = AdminSettings()
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def update_password(db: Session, admin: AdminUser, new_password: str) -> None:
    admin.hashed_password = hash_password(new_password)
    db.commit()


def update_profile(db: Session, admin: AdminUser, full_name: str, email: str, phone: str) -> AdminUser:
    admin.full_name = full_name
    admin.email = email
    admin.phone = phone
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That email is already in use by another account")
    db.refresh(admin)
    return admin


def get_or_create_settings(db: Session, admin: AdminUser) -> AdminSettings:
    if admin.settings is None:
        admin.settings = AdminSettings(admin_user_id=admin.id)
        db.add(admin.settings)
        db.commit()
        db.refresh(admin)
    return admin.settings


def update_branding(db: Session, admin: AdminUser, logo_url: str) -> AdminSettings:
    settings_row = get_or_create_settings(db, admin)
    settings_row.logo_url = logo_url
    db.commit()
    db.refresh(settings_row)
    return settings_row


def update_notifications(db: Session, admin: AdminUser, new_booking: bool, payments: bool, reviews: bool, marketing: bool) -> AdminSettings:
    settings_row = get_or_create_settings(db, admin)
    settings_row.notify_new_booking = new_booking
    settings_row.notify_payments = payments
    settings_row.notify_reviews = reviews
    settings_row.notify_marketing = marketing
    db.commit()
    db.refresh(settings_row)
    return settings_row
