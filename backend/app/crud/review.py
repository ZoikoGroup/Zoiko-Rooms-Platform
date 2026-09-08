from datetime import date as date_

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.crud import notification as notif_crud
from app.crud.ids import new_id
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.review import Review
from app.schemas.review import ReviewCreate, ReviewRead


def list_reviews_for_listing(db: Session, listing_id: str) -> list[ReviewRead]:
    reviews = db.scalars(
        select(Review)
        .options(joinedload(Review.listing), joinedload(Review.guest))
        .where(Review.listing_id == listing_id)
        .order_by(Review.date.desc())
    )
    return [to_review_read(r) for r in reviews]


def guest_has_stayed_at_listing(db: Session, guest_id: str, listing_id: str) -> bool:
    """Eligibility rule: a guest may review a listing only after their stay has
    actually ended -- matches how every mainstream rental/marketplace platform
    gates reviews (post-checkout, not before or during the stay)."""
    return (
        db.scalar(
            select(Occupancy.id)
            .where(Occupancy.guest_id == guest_id, Occupancy.listing_id == listing_id, Occupancy.status == "ENDED")
            .limit(1)
        )
        is not None
    )


def _recompute_listing_rating(db: Session, listing: Listing) -> None:
    """Single source of truth for Listing.rating/review_count. A listing with
    zero reviews has no real rating -- 0.0 here is a null-ish placeholder the
    frontend must gate on review_count == 0, never displayed as a star score."""
    avg_rating, count = db.execute(
        select(func.avg(Review.rating), func.count(Review.id)).where(Review.listing_id == listing.id)
    ).one()
    listing.rating = round(float(avg_rating), 2) if avg_rating is not None else 0.0
    listing.review_count = count or 0


def create_review(db: Session, guest_id: str, data: ReviewCreate) -> Review:
    if not (1 <= data.rating <= 5):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Rating must be between 1 and 5")

    listing = db.get(Listing, data.listing_id)
    if not listing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")

    if not guest_has_stayed_at_listing(db, guest_id, data.listing_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You can only review a listing after your stay there has ended"
        )

    existing = db.scalar(
        select(Review).where(Review.guest_id == guest_id, Review.listing_id == data.listing_id)
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "You have already reviewed this listing")

    review = Review(
        id=new_id("RV"),
        listing_id=data.listing_id,
        guest_id=guest_id,
        rating=data.rating,
        comment=data.comment.strip(),
        date=date_.today(),
    )
    db.add(review)
    db.flush()
    _recompute_listing_rating(db, listing)

    if listing.room and listing.room.property:
        notif_crud.notify_user_by_party(
            db, listing.room.property.owner_party_id,
            title="New review received",
            message=f"{review.guest.name} left a {review.rating}-star review on {listing.name}.",
            notification_type="review.created",
            related_entity_type="listing", related_entity_id=listing.id,
        )

    db.commit()
    db.refresh(review)
    return review


def list_reviews(db: Session) -> list[ReviewRead]:
    reviews = db.scalars(
        select(Review).options(joinedload(Review.listing), joinedload(Review.guest)).order_by(Review.date.desc())
    )
    return [to_review_read(r) for r in reviews]


def to_review_read(review: Review) -> ReviewRead:
    return ReviewRead(
        id=review.id,
        listing_id=review.listing_id,
        listing_name=review.listing.name,
        guest_name=review.guest.name,
        guest_avatar=review.guest.avatar,
        rating=review.rating,
        comment=review.comment,
        date=review.date,
        property_type=review.listing.property_type,
    )
