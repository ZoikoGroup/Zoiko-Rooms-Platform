from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.admin_user import AdminSettings, AdminUser
from app.models.booking import Booking
from app.models.listing import Listing
from app.models.payment import Payment
from app.models.review import Review
from app.schemas.admin_user import AdminUserCreate, AdminUserUpdate


def list_admin_users(db: Session) -> list[AdminUser]:
    return list(db.scalars(select(AdminUser).order_by(AdminUser.created_at)))


def get_admin_user(db: Session, admin_id: int) -> AdminUser | None:
    return db.get(AdminUser, admin_id)


def count_active_super_admins(db: Session) -> int:
    return db.scalar(
        select(func.count(AdminUser.id)).where(AdminUser.role == "super_admin", AdminUser.is_active.is_(True))
    )


def create_admin_user(db: Session, data: AdminUserCreate) -> AdminUser:
    admin = AdminUser(
        email=data.email,
        hashed_password=hash_password(data.password),
        full_name=data.full_name,
        phone=data.phone,
        role=data.role,
        dispute_role=data.dispute_role,
        payment_staff_role=data.payment_staff_role,
    )
    admin.settings = AdminSettings()
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def update_admin_user(db: Session, target: AdminUser, data: AdminUserUpdate, acting_admin: AdminUser) -> AdminUser:
    demoting_or_deactivating = (data.role is not None and data.role != "super_admin") or data.is_active is False

    if target.id == acting_admin.id and demoting_or_deactivating:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't change your own role or deactivate yourself")

    if target.role == "super_admin" and demoting_or_deactivating and count_active_super_admins(db) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one active super admin must remain")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(target, field, value)
    db.commit()
    db.refresh(target)
    return target


def set_admin_approval_status(db: Session, target: AdminUser, approval_status: str) -> AdminUser:
    target.approval_status = approval_status
    db.commit()
    db.refresh(target)
    return target


def count_owned_listings(db: Session, admin_id: int) -> int:
    return db.scalar(select(func.count(Listing.id)).where(Listing.owner_id == admin_id))


def delete_admin_user(
    db: Session,
    target: AdminUser,
    acting_admin: AdminUser,
    reassign_to_id: int | None = None,
    force: bool = False,
) -> None:
    if target.id == acting_admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't delete your own account")

    if target.role == "super_admin" and count_active_super_admins(db) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one active super admin must remain")

    owned_listings = list(db.scalars(select(Listing).where(Listing.owner_id == target.id)))

    if owned_listings and reassign_to_id is not None:
        new_owner = get_admin_user(db, reassign_to_id)
        if not new_owner:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Reassignment target admin not found")
        if new_owner.id == target.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot reassign listings to the account being deleted")
        for listing in owned_listings:
            listing.owner_id = new_owner.id
        db.flush()
    elif owned_listings and force:
        for listing in owned_listings:
            bookings = list(db.scalars(select(Booking).where(Booking.listing_id == listing.id)))
            for booking in bookings:
                payment = db.scalar(select(Payment).where(Payment.booking_id == booking.id))
                if payment:
                    db.delete(payment)
                db.delete(booking)
            reviews = list(db.scalars(select(Review).where(Review.listing_id == listing.id)))
            for review in reviews:
                db.delete(review)
            db.delete(listing)
        db.flush()
    elif owned_listings:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This admin owns {len(owned_listings)} listing(s). Reassign or delete them before removing the account.",
        )

    db.delete(target)
    db.commit()
