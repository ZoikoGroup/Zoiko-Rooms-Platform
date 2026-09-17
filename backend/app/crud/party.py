from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin_user import AdminUser
from app.models.membership import Membership
from app.models.party import Party


def get_default_membership(db: Session, admin: AdminUser) -> Membership | None:
    return db.scalar(
        select(Membership).where(Membership.admin_user_id == admin.id, Membership.status == "active").order_by(Membership.id)
    )


def get_or_create_default_party(db: Session, admin: AdminUser) -> Party:
    """Every admin needs a Party to own Properties under. Providers (role="admin") and
    Zoiko-internal super admins get one auto-provisioned on first use rather than
    requiring a separate "create your organization" step -- there's no self-service
    provider signup yet, so this keeps the existing admin-invite flow working unchanged."""
    membership = get_default_membership(db, admin)
    if membership:
        return membership.party

    party_type = "zoiko_operator" if admin.role == "super_admin" else "provider"
    party = Party(party_type=party_type)
    db.add(party)
    db.flush()

    membership = Membership(admin_user_id=admin.id, party_id=party.id, role="provider_owner_admin")
    db.add(membership)
    db.flush()
    return party


def assert_provider_access(db: Session, admin: AdminUser, party_id: int, roles: tuple[str, ...] | None = None) -> None:
    """Authorizes a provider-side action against the Party/Membership model -- the
    real organizational relationship -- rather than Listing.owner_id (a direct,
    single-AdminUser FK that can't express roles like provider_finance at all).
    super_admin always passes. `roles`, if given, additionally requires the
    membership's role to be one of them (e.g. finance-sensitive actions)."""
    if admin.role == "super_admin":
        return

    membership = db.scalar(
        select(Membership).where(
            Membership.admin_user_id == admin.id,
            Membership.party_id == party_id,
            Membership.status == "active",
        )
    )
    if not membership or (roles and membership.role not in roles):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to manage this provider's records")


def party_id_for_listing(listing: "Listing") -> int:
    """The authorizing party for any listing/room action is always the property's
    owning party -- shared across the leasing/occupancy/finance domains."""
    if not listing.room:
        raise HTTPException(status.HTTP_409_CONFLICT, "Listing is not linked to a room")
    return listing.room.property.owner_party_id


def party_id_for_room(room: "Room") -> int:
    """Same authorizing party as party_id_for_listing, resolved directly from a room
    for domains (room passport, authority records) that key off room_id rather than
    a listing."""
    return room.property.owner_party_id
