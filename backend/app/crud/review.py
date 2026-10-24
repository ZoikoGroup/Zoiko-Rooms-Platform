from datetime import date as date_

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

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


def list_reviews_by_guest(db: Session, guest_id: str) -> list[ReviewRead]:
    """The reviews one renter has written -- lets their own screen show a
    listing as already reviewed instead of offering the form again."""
    reviews = db.scalars(
        select(Review)
        .options(joinedload(Review.listing), joinedload(Review.guest))
        .where(Review.guest_id == guest_id)
        .order_by(Review.date.desc())
    )
    return [to_review_read(r) for r in reviews]


def guest_has_stayed_at_listing(db: Session, guest_id: str, listing_id: str) -> bool:
    """Eligibility rule: a guest may review a listing only if an Occupancy row
    ties them to it -- i.e. they were actually a tenant there, current or past.
    Not an invented rule -- it's the same guest<->listing relationship the
    rental lifecycle already establishes, just checked before allowing a write."""
    return (
        db.scalar(
            select(Occupancy.id)
            .where(Occupancy.guest_id == guest_id, Occupancy.listing_id == listing_id)
            .limit(1)
        )
        is not None
    )


def _recompute_listing_rating(db: Session, listing: Listing) -> None:
    """Single source of truth for Listing.rating/review_count. Replaces the
    static 4.5/0 values set at listing creation with a real aggregate over
    this listing's actual reviews every time the set of reviews changes."""
    avg_rating, count = db.execute(
        select(func.avg(Review.rating), func.count(Review.id)).where(Review.listing_id == listing.id)
    ).one()
    listing.rating = round(float(avg_rating), 2) if avg_rating is not None else 4.5
    listing.review_count = count or 0


def create_review(db: Session, guest_id: str, data: ReviewCreate) -> Review:
    if not (1 <= data.rating <= 5):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Rating must be between 1 and 5")

    listing = db.get(Listing, data.listing_id)
    if not listing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")

    if not guest_has_stayed_at_listing(db, guest_id, data.listing_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only review a listing you have rented")

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
