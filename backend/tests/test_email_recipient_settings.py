"""Emails follow the recipient's own settings (services/email/recipient.py):
notification opt-outs, and their time zone / clock / region
for dates."""

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.core import mailer
from app.crud import user_settings
from app.models.notification_preference import NotificationPreference
from app.services.email import formatting as fmt
from app.services.email import message_log
from app.services.email import recipient as recipient_prefs
from tests.conftest import _make_user


@pytest.fixture
def logged(db_session: Session, monkeypatch):
    db_session._zr_owned_by_log = False
    monkeypatch.setattr(message_log, "_session_factory", lambda: db_session)
    sent: list[str] = []
    monkeypatch.setattr(mailer, "_write_to_dev_outbox", lambda to, subject, text, html: sent.append(subject))
    return sent


def _prefs(db: Session, user, **notif):
    db.add(NotificationPreference(recipient_type="user", recipient_user_id=user.id, recipient_key=f"user:{user.id}", **notif))
    db.commit()


class TestEmailDelivery:
    def test_opted_out_category_is_not_emailed_but_security_still_is(self, db_session: Session, logged):
        user = _make_user(db_session, email="email-optout@test.com")
        _prefs(db_session, user, opted_out_categories=["PAYMENTS"])

        from app.services.email import registry
        from app.services.email.design import Message

        reminder = Message(
            spec=registry.get("ZR-EML-RENT-001"), subject_vars={"amount_localized": "£500", "due_date_local": "1 Nov"},
            heading="Rent due", related_entity_type="rental_payment_obligation",
        )
        mailer.deliver(user.email, reminder)
        assert logged == [], "rent reminders are PAYMENTS, which this user opted out of"

        profile = recipient_prefs.lookup(user.email)
        assert recipient_prefs.suppression_reason(profile, tier=1, stream_key="money", related_entity_type="deposit_record")
        assert recipient_prefs.suppression_reason(profile, tier=0, stream_key="money", related_entity_type="deposit_record") == ""
        assert recipient_prefs.suppression_reason(profile, tier=1, stream_key="security", related_entity_type="") == ""

        mailer.send_password_reset_email(user.email, "https://x/reset", 30, "Test User")
        assert logged, "a security email is never suppressed"

    def test_old_quiet_hours_never_hold_an_email_back(self, db_session: Session, logged):
        user = _make_user(db_session, email="email-quiet@test.com")
        # A window saved before quiet hours were removed (ZR-61) is ignored.
        _prefs(db_session, user, quiet_hours_enabled=True, quiet_hours_start_minute=0, quiet_hours_end_minute=1439)
        profile = recipient_prefs.lookup(user.email)
        assert recipient_prefs.suppression_reason(
            profile, tier=2, stream_key="money", related_entity_type="deposit_record") == ""

    def test_unknown_address_is_never_suppressed(self, db_session: Session, logged):
        assert recipient_prefs.lookup("nobody@nowhere.test") is None
        assert recipient_prefs.suppression_reason(None, tier=2, stream_key="money", related_entity_type="payment") == ""


class TestEmailDates:
    def test_dates_follow_time_zone_clock_and_region(self):
        moment = datetime(2026, 10, 15, 18, 5, tzinfo=timezone.utc)
        assert fmt.moment(moment) == "15 October 2026, 18:05 UTC"
        profile = recipient_prefs.RecipientProfile(time_zone="America/New_York", time_format="12h", region="US")
        with fmt.for_recipient(profile):
            assert fmt.moment(moment) == "October 15, 2026, 2:05 PM EDT"
            assert fmt.day(datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)) == "October 15, 2026"
        assert fmt.moment(moment) == "15 October 2026, 18:05 UTC", "settings don't leak past the email"
