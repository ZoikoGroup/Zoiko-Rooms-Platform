from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.crud import notification as notif_crud
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


def create_admin_user(db: Session, data: AdminUserCreate, acting_admin: AdminUser) -> AdminUser:
    admin = AdminUser(
        email=data.email,
        hashed_password=hash_password(data.password),
        full_name=data.full_name,
        phone=data.phone,
        role=data.role,
    )
    admin.settings = AdminSettings()
    db.add(admin)
    db.flush()

    notif_crud.notify_admin(
        db, admin.id,
        title="Your admin account was created",
        message=f"{acting_admin.full_name} added you as a {data.role.replace('_', ' ')}.",
        notification_type="admin_user.created",
        related_entity_type="admin_user", related_entity_id=str(admin.id),
    )
    other_super_admin_ids = db.scalars(
        select(AdminUser.id).where(
            AdminUser.role == "super_admin", AdminUser.is_active.is_(True), AdminUser.id != acting_admin.id
        )
    )
    for super_admin_id in other_super_admin_ids:
        notif_crud.notify_admin(
            db, super_admin_id,
            title="New team member added",
            message=f"{acting_admin.full_name} added {admin.full_name} ({data.role.replace('_', ' ')}).",
            notification_type="admin_user.created",
            related_entity_type="admin_user", related_entity_id=str(admin.id),
        )

    db.commit()
    db.refresh(admin)
    return admin


def update_admin_user(db: Session, target: AdminUser, data: AdminUserUpdate, acting_admin: AdminUser) -> AdminUser:
    demoting_or_deactivating = (data.role is not None and data.role != "super_admin") or data.is_active is False

    if target.id == acting_admin.id and demoting_or_deactivating:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't change your own role or deactivate yourself")

    if target.role == "super_admin" and demoting_or_deactivating and count_active_super_admins(db) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one active super admin must remain")

    changes = data.model_dump(exclude_unset=True)
    role_changed = "role" in changes and changes["role"] != target.role
    deactivated = changes.get("is_active") is False and target.is_active
    reactivated = changes.get("is_active") is True and not target.is_active

    for field, value in changes.items():
        setattr(target, field, value)
    db.flush()

    if role_changed:
        notif_crud.notify_admin(
            db, target.id,
            title="Your role was changed",
            message=f"{acting_admin.full_name} changed your role to {target.role.replace('_', ' ')}.",
            notification_type="admin_user.role_changed",
            related_entity_type="admin_user", related_entity_id=str(target.id),
        )
    if deactivated:
        notif_crud.notify_admin(
            db, target.id,
            title="Your account was deactivated",
            message=f"{acting_admin.full_name} deactivated your admin account.",
            notification_type="admin_user.deactivated",
            related_entity_type="admin_user", related_entity_id=str(target.id),
        )
    elif reactivated:
        notif_crud.notify_admin(
            db, target.id,
            title="Your account was reactivated",
            message=f"{acting_admin.full_name} reactivated your admin account.",
            notification_type="admin_user.reactivated",
            related_entity_type="admin_user", related_entity_id=str(target.id),
        )

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
