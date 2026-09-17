"""Matches active room alerts against newly-published listings and emails
subscribers. Intended to run on a schedule (e.g. a Render cron job) -- this
backend has no in-process job scheduler, so this is a standalone script in
the same spirit as seed.py rather than a background task.

Run with: python check_alerts.py
"""
from datetime import datetime, timezone

from app.core.config import settings
from app.core.mailer import send_alert_match_email
from app.crud.room_alert import list_active_alerts, mark_notified
from app.db.session import SessionLocal
from app.models.listing import Listing


def _matches(listing: Listing, alert) -> bool:
    if listing.city.strip().lower() != alert.city.strip().lower():
        return False
    if alert.min_price is not None and listing.price_per_night < alert.min_price:
        return False
    if alert.max_price is not None and listing.price_per_night > alert.max_price:
        return False
    if alert.room_type and alert.room_type.strip().lower() != listing.room_type.strip().lower():
        return False
    return True


def check_alerts() -> None:
    db = SessionLocal()
    try:
        alerts = list_active_alerts(db)
        if not alerts:
            return

        published = (
            db.query(Listing)
            .filter(Listing.state == "PUBLISHED", Listing.published_at.is_not(None))
            .all()
        )

        now = datetime.now(timezone.utc)
        for alert in alerts:
            since = alert.last_notified_at or alert.created_at
            new_matches = [
                listing for listing in published
                if listing.published_at > since and _matches(listing, alert)
            ]
            if not new_matches:
                continue

            unsubscribe_url = (
                f"{settings.public_api_url}/api/public/alerts/{alert.id}/unsubscribe"
                f"?token={alert.unsubscribe_token}"
            )
            send_alert_match_email(
                alert.email,
                alert.city,
                [listing.name for listing in new_matches],
                unsubscribe_url,
            )
            mark_notified(db, alert, now)
    finally:
        db.close()


if __name__ == "__main__":
    check_alerts()
