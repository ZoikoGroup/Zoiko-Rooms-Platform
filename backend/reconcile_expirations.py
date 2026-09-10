"""ZR-ENG-CLR-001 Section 1, Rule 7 / Section 13: reconciliation safety net for
the two lazy expiry clocks (app/services/booking_expiry.py) -- the 24-hour
accepted-booking confirmation window and the 30-minute payment checkout lock.

Both clocks already self-heal lazily whenever the affected Offer/Agreement is
read or acted on (get_offer_or_404/get_agreement_or_404), so this script is a
safety net, not the primary mechanism: it catches an overdue offer/agreement
that nobody happens to read again, freeing its room hold promptly instead of
leaving it stuck until the next incidental read.

Intended to run on a schedule (e.g. a Render cron job) -- this backend has no
in-process job scheduler, so this is a standalone script in the same spirit as
check_alerts.py rather than a background task.

Run with: python reconcile_expirations.py
"""
from app.db.session import SessionLocal
from app.services.booking_expiry import sweep_expired_checkouts, sweep_expired_offers


def reconcile_expirations() -> None:
    db = SessionLocal()
    try:
        expired_offers = sweep_expired_offers(db)
        expired_checkouts = sweep_expired_checkouts(db)
        print(f"Expired {len(expired_offers)} overdue offer(s), {len(expired_checkouts)} overdue checkout(s).")
    finally:
        db.close()


if __name__ == "__main__":
    reconcile_expirations()
