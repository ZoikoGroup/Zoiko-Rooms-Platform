from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user_optional
from app.crud import notification as notif_crud
from app.crud.leasing import submit_application, to_application_read
from app.crud.listing import get_listing, is_listing_available, list_public_listings, to_public_listing_read
from app.crud.review import list_reviews_for_listing
from app.db.session import get_db
from app.models.user_account import UserAccount
from app.schemas.leasing import ApplicationCreate, ApplicationRead
from app.schemas.listing import PublicListingRead, PublicListingsPage
from app.schemas.review import ReviewRead

router = APIRouter(prefix="/api/public", tags=["public"])

MAX_PUBLIC_LISTINGS_LIMIT = 100


@router.get("/listings", response_model=PublicListingsPage)
def get_public_listings(
    city: str | None = None,
    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),
    room_type: str | None = None,
    amenities: str | None = Query(default=None, description="Comma-separated list of required amenities"),
    limit: int = Query(default=20, ge=1, le=MAX_PUBLIC_LISTINGS_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: UserAccount | None = Depends(get_current_user_optional),
):
    amenity_list = [a.strip() for a in amenities.split(",") if a.strip()] if amenities else None
    listings, total = list_public_listings(
        db,
        city=city,
        min_price=min_price,
        max_price=max_price,
        room_type=room_type,
        amenities=amenity_list,
        limit=limit,
        offset=offset,
        # A signed-in USER never sees a listing hosted by their own party in
        # their own search results -- they can't rent a room they list themselves.
        exclude_party_id=user.party_id if user else None,
    )
    return PublicListingsPage(
        items=[to_public_listing_read(listing) for listing in listings],
        limit=limit,
        offset=offset,
        total=total,
        has_more=offset + len(listings) < total,
    )


@router.get("/listings/{listing_id}", response_model=PublicListingRead)
def get_public_listing(
    listing_id: str,
    db: Session = Depends(get_db),
    user: UserAccount | None = Depends(get_current_user_optional),
):
    """Single-listing detail view. Same visibility rule as the list endpoint --
    only ever returns a PUBLISHED listing with an available room, so a
    draft/paused/withdrawn/occupied listing is never reachable by guessing its
    id. Also matches the list endpoint's self-listing exclusion: a signed-in
    USER gets 404 (not a distinguishing error) for a listing their own party
    hosts, exactly as if it didn't exist."""
    listing = get_listing(db, listing_id)
    if not listing or not is_listing_available(db, listing):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")
    if user and user.party_id and listing.party_id == user.party_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")
    return to_public_listing_read(listing)


@router.get("/listings/{listing_id}/reviews", response_model=list[ReviewRead])
def get_public_listing_reviews(listing_id: str, db: Session = Depends(get_db)):
    """Real reviews for a listing -- backs the listing detail page's rating/
    review display. Same visibility rule as the listing itself: a listing that
    doesn't currently resolve to a real one still returns an empty list rather
    than leaking existence via a 404/200 split, since review content isn't
    sensitive the way full listing details are."""
    return list_reviews_for_listing(db, listing_id)


@router.post("/applications", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
def post_application(payload: ApplicationCreate, db: Session = Depends(get_db)):
    """No auth -- the separate renter-facing website submits directly on a renter's
    behalf, same as the existing new-guest booking flow."""
    application = submit_application(db, payload)

    listing = get_listing(db, application.listing_id)
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="New rental application",
            message=f'A new application was submitted for your listing "{listing.name}".',
            notification_type="application.received",
            related_entity_type="application", related_entity_id=str(application.id),
        )
    notif_crud.notify_all_super_admins(
        db,
        title="New rental application submitted",
        message=f"A new application was submitted for listing {application.listing_id}.",
        notification_type="application.submitted",
        related_entity_type="application", related_entity_id=str(application.id),
    )
    db.commit()

    return to_application_read(application)
