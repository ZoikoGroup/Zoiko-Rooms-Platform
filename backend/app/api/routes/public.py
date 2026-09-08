from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user_optional
from app.core.mailer import send_alert_confirmation_email
from app.crud.leasing import submit_application, to_application_read
from app.crud.listing import get_listing, is_listing_available, list_public_listings, to_public_listing_read
from app.crud.review import list_reviews_for_listing
from app.crud.room_alert import create_alert, unsubscribe_alert
from app.db.session import get_db
from app.models.user_account import UserAccount
from app.schemas.leasing import ApplicationCreate, ApplicationRead
from app.schemas.listing import PublicListingRead, PublicListingsPage
from app.schemas.review import ReviewRead
from app.schemas.room_alert import RoomAlertCreate, RoomAlertRead
from app.core.config import settings

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
    return to_application_read(submit_application(db, payload))


@router.post("/alerts", response_model=RoomAlertRead, status_code=status.HTTP_201_CREATED)
def post_alert(payload: RoomAlertCreate, db: Session = Depends(get_db)):
    """No auth -- reached from the marketing site's "Save City Alert"/"Create a
    free alert" buttons, before a visitor has any account. Matching new
    PUBLISHED listings against active alerts and emailing subscribers is a
    separate scheduled job (see check_alerts.py), not done here."""
    if not payload.email.strip() or "@" not in payload.email:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A valid email is required")
    if not payload.city.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "city is required")

    alert = create_alert(db, payload)
    unsubscribe_url = f"{settings.public_api_url}/api/public/alerts/{alert.id}/unsubscribe?token={alert.unsubscribe_token}"
    send_alert_confirmation_email(alert.email, alert.city, unsubscribe_url)
    return alert


def _alert_page(*, heading: str, message: str) -> str:
    """A human clicks this link straight from an email client -- it must render
    as a page, not the raw JSON the rest of this API returns."""
    return f"""<!DOCTYPE html>
<html>
  <head><meta charset="utf-8"><title>Zoiko Rooms</title></head>
  <body style="margin:0;padding:32px 16px;background:#f1f5f9;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
    <table role="presentation" width="100%" style="max-width:480px;margin:0 auto;background:#ffffff;border-radius:16px;overflow:hidden;">
      <tr><td style="background:#0e2f73;padding:24px 32px;">
        <span style="color:#ffffff;font-size:18px;font-weight:800;">Zoiko Rooms</span>
      </td></tr>
      <tr><td style="padding:32px;">
        <h1 style="margin:0 0 16px;color:#0f172a;font-size:20px;font-weight:800;">{heading}</h1>
        <p style="margin:0 0 24px;color:#334155;font-size:15px;line-height:1.5;">{message}</p>
        <a href="{settings.frontend_url}/find-a-room" style="background:#0e2f73;color:#ffffff;text-decoration:none;
           padding:12px 24px;border-radius:9999px;font-weight:600;font-size:14px;display:inline-block;">
          Browse rooms
        </a>
      </td></tr>
    </table>
  </body>
</html>"""


@router.get("/alerts/{alert_id}/unsubscribe", response_class=HTMLResponse)
def get_alert_unsubscribe(alert_id: str, token: str, db: Session = Depends(get_db)):
    """No auth -- the token itself (from the alert's own confirmation/match
    emails) is what proves the caller owns this alert. Reached by a human
    clicking the link directly in their email client, so it renders a page
    rather than the raw JSON the rest of this API returns."""
    if not unsubscribe_alert(db, alert_id, token):
        return HTMLResponse(
            _alert_page(
                heading="Link no longer valid",
                message="This unsubscribe link has already been used or is invalid.",
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return HTMLResponse(
        _alert_page(
            heading="You've been unsubscribed",
            message="You won't receive any more room alert emails for this subscription.",
        )
    )
