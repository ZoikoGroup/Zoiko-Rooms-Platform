from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.correlation import get_correlation_id
from app.core.image_uploads import save_listing_images
from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.crud import listing as listing_crud
from app.crud.property import get_property, list_rooms_for_property
from app.db.session import get_db
from app.models.user_account import UserAccount
from app.schemas.marketplace import PropertyCreate, PropertyRead, RoomCreate, RoomRead
from app.schemas.listing import ListingCreate, ListingRead, ListingUpdate

router = APIRouter(prefix="/api/users/hosting", tags=["user-hosting"], dependencies=[Depends(get_current_user)])


@router.post("/uploads/images")
async def upload_user_listing_images(
    files: list[UploadFile],
    user: UserAccount = Depends(get_current_user),
):
    """Lets a host upload photos for their own listings. USER-authenticated
    (get_current_user) -- deliberately never get_current_admin, and shares
    validation/storage with the admin upload endpoint via save_listing_images
    rather than duplicating it. Stores into the same PUBLIC upload_dir as the
    admin path; identity documents never pass through here."""
    urls = await save_listing_images(files)
    return {"urls": urls}


def _get_property_or_404(db: Session, property_id: int, user: UserAccount):
    """Get property and verify ownership."""
    prop = get_property(db, property_id)
    if not prop:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Property not found")
    if not user.party_id or prop.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only manage your own properties")
    return prop


@router.get("/properties", response_model=list[PropertyRead])
def list_properties(
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all properties owned by current user."""
    if not user.party_id:
        return []
    
    from sqlalchemy import select
    from app.models.property import Property

    properties = list(
        db.scalars(select(Property).where(Property.owner_party_id == user.party_id))
    )
    return properties


@router.post("/properties", response_model=PropertyRead, status_code=status.HTTP_201_CREATED)
def create_user_property(
    payload: PropertyCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new property as a host."""
    if not user.party_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no associated party")
    
    from app.models.property import Property

    prop = Property(
        owner_party_id=user.party_id,
        address=payload.address,
        city=payload.city,
        status="active",
    )
    db.add(prop)
    db.commit()
    db.refresh(prop)

    log_audit_event(db, None, "user_property.create", "property", str(prop.id), get_correlation_id(request), reason=f"user:{user.id}")
    db.commit()
    return prop


@router.put("/properties/{property_id}", response_model=PropertyRead)
def update_user_property(
    property_id: int,
    payload: PropertyCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a property."""
    prop = _get_property_or_404(db, property_id, user)
    
    prop.address = payload.address
    prop.city = payload.city
    db.commit()
    db.refresh(prop)

    log_audit_event(db, None, "user_property.update", "property", str(property_id), get_correlation_id(request), reason=f"user:{user.id}")
    db.commit()
    return prop


@router.get("/properties/{property_id}/rooms", response_model=list[RoomRead])
def list_property_rooms(
    property_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all rooms in a property."""
    prop = _get_property_or_404(db, property_id, user)
    return list_rooms_for_property(db, property_id)


@router.post("/properties/{property_id}/rooms", response_model=RoomRead, status_code=status.HTTP_201_CREATED)
def create_user_room(
    property_id: int,
    payload: RoomCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add a room to a property."""
    prop = _get_property_or_404(db, property_id, user)
    
    from app.models.room import Room

    room = Room(
        property_id=prop.id,
        room_type="private_room",
        size=payload.size,
        has_ensuite=payload.has_ensuite,
        status="active",
    )
    db.add(room)
    db.commit()
    db.refresh(room)

    log_audit_event(db, None, "user_room.create", "room", str(room.id), get_correlation_id(request), reason=f"user:{user.id}")
    db.commit()
    return room


@router.put("/properties/{property_id}/rooms/{room_id}", response_model=RoomRead)
def update_user_room(
    property_id: int,
    room_id: int,
    payload: RoomCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a room in a property."""
    prop = _get_property_or_404(db, property_id, user)
    
    from app.models.room import Room

    room = db.get(Room, room_id)
    if not room or room.property_id != prop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")

    room.size = payload.size
    room.has_ensuite = payload.has_ensuite
    db.commit()
    db.refresh(room)

    log_audit_event(db, None, "user_room.update", "room", str(room_id), get_correlation_id(request), reason=f"user:{user.id}")
    db.commit()
    return room


def _get_user_listing_or_404(db: Session, listing_id: str, user: UserAccount):
    listing = listing_crud.get_listing(db, listing_id)
    if not listing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")
    if not user.party_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no associated party")
    listing_crud.assert_party_owns_listing(listing, user.party_id)
    return listing


@router.get("/listings", response_model=list[ListingRead])
def list_user_listings(
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List every listing (draft or published) owned by the current user's party.
    Backs the "My Listings" page -- previously there was no way to read a host's
    own drafts back, so the frontend cached write results in localStorage as a
    stopgap. That stopgap is no longer needed now that this exists."""
    if not user.party_id:
        return []
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload
    from app.models.listing import Listing

    listings = list(
        db.scalars(
            select(Listing)
            .options(joinedload(Listing.room))
            .where(Listing.party_id == user.party_id)
            .order_by(Listing.id.desc())
        )
    )
    return listing_crud.annotate_availability(db, listings)


@router.post("/listings", response_model=ListingRead, status_code=status.HTTP_201_CREATED)
def create_user_listing(
    payload: ListingCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.party_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no associated party")
    listing = listing_crud.create_listing_for_party(db, payload, user.party_id)
    log_audit_event(db, None, "user_listing.create", "listing", listing.id, get_correlation_id(request), reason=f"user:{user.id}")
    db.commit()
    return listing


@router.put("/listings/{listing_id}", response_model=ListingRead)
def update_user_listing(
    listing_id: str,
    payload: ListingUpdate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    listing = _get_user_listing_or_404(db, listing_id, user)
    if payload.room_id is not None:
        listing_crud.assert_party_owns_room(db, payload.room_id, user.party_id)
    updated = listing_crud.update_listing(db, listing, payload)
    log_audit_event(db, None, "user_listing.update", "listing", listing_id, get_correlation_id(request), reason=f"user:{user.id}")
    db.commit()
    return updated


@router.get("/listings/{listing_id}/publish-eligibility")
def get_user_listing_publish_eligibility(
    listing_id: str,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    listing = _get_user_listing_or_404(db, listing_id, user)
    reasons = listing_crud.check_publish_eligibility(db, listing)
    return {"eligible": not reasons, "reasons": reasons}


@router.post("/listings/{listing_id}/submit-for-review", response_model=ListingRead)
def submit_user_listing_for_review(
    listing_id: str,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A host can only ask for review -- publishing is always an explicit
    admin/super-admin decision (see listings.py's publish/reject endpoints)."""
    listing = _get_user_listing_or_404(db, listing_id, user)
    updated = listing_crud.submit_listing_for_review(db, listing)
    log_audit_event(
        db, None, "user_listing.submit_for_review", "listing", listing_id, get_correlation_id(request),
        reason=f"user:{user.id}",
    )
    emit_event(db, "listing.submitted", "listing", listing_id, {"room_id": updated.room_id, "partyId": user.party_id})
    db.commit()
    return updated
