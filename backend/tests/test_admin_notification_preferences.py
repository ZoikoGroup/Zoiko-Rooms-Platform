"""Bug ZR-20: an admin's Settings > Notification Preferences switches were
saved but never read, so no toggle changed what they were notified about."""

from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.models.admin_user import AdminSettings
from app.models.notification import Notification
from tests.conftest import _make_admin, auth_admin_cookie


def _notify(db: Session, admin, notification_type: str, entity: str = "1"):
    return notif_crud.notify_admin(
        db, admin.id, title="t", message="m", notification_type=notification_type,
        related_entity_type="x", related_entity_id=entity,
    )


def _set(db: Session, admin, **flags):
    row = db.query(AdminSettings).filter_by(admin_user_id=admin.id).one_or_none() or AdminSettings(admin_user_id=admin.id)
    for name, value in flags.items():
        setattr(row, f"notify_{name}", value)
    db.add(row)
    db.commit()


class TestAdminNotificationPreferences:
    def test_defaults_are_bookings_and_payments_on_reviews_and_marketing_off(self, db_session: Session):
        admin = _make_admin(db_session, email="np-default@test.com")
        db_session.commit()
        assert _notify(db_session, admin, "application.received") is not None
        assert _notify(db_session, admin, "payment.confirmed") is not None
        assert _notify(db_session, admin, "review.new") is None
        assert _notify(db_session, admin, "marketing.update") is None

    def test_each_switch_controls_its_own_kind_of_notification(self, db_session: Session):
        admin = _make_admin(db_session, email="np-switch@test.com")
        _set(db_session, admin, new_booking=False, payments=False, reviews=True, marketing=True)
        assert _notify(db_session, admin, "application.received") is None
        assert _notify(db_session, admin, "occupancy.move_out_notice_for_host") is None
        assert _notify(db_session, admin, "payment.confirmed") is None
        assert _notify(db_session, admin, "listing_fee.paid") is None
        assert _notify(db_session, admin, "review.new") is not None
        assert _notify(db_session, admin, "marketing.update") is not None

        _set(db_session, admin, new_booking=True, payments=True, reviews=False, marketing=False)
        assert _notify(db_session, admin, "application.received", "2") is not None
        assert _notify(db_session, admin, "payment.confirmed", "2") is not None
        assert _notify(db_session, admin, "review.new", "2") is None
        assert _notify(db_session, admin, "marketing.update", "2") is None

    def test_safety_and_operational_notices_are_never_switched_off(self, db_session: Session):
        admin = _make_admin(db_session, email="np-always@test.com")
        _set(db_session, admin, new_booking=False, payments=False, reviews=False, marketing=False)
        for kind in ("dispute_case.opened_admin", "dispute.opened_admin", "listing.submitted",
                     "property_verification.review_needed", "identity_verification.expiry_due", "support_ticket.reply"):
            assert _notify(db_session, admin, kind) is not None, kind

    def test_one_admins_switch_does_not_affect_another(self, db_session: Session):
        quiet = _make_admin(db_session, email="np-quiet@test.com")
        loud = _make_admin(db_session, email="np-loud@test.com")
        _set(db_session, quiet, payments=False)
        assert _notify(db_session, quiet, "payment.confirmed") is None
        assert _notify(db_session, loud, "payment.confirmed") is not None

    def test_saving_the_toggles_changes_what_arrives(self, client, db_session: Session):
        admin = _make_admin(db_session, email="np-api@test.com")
        db_session.commit()
        cookies = auth_admin_cookie(admin)
        r = client.put(
            "/api/settings/notifications", json={"newBooking": True, "payments": False, "reviews": True, "marketing": False},
            cookies=cookies,
        )
        assert r.status_code == 200, r.text
        assert _notify(db_session, admin, "payment.confirmed") is None
        assert _notify(db_session, admin, "review.new") is not None
        assert db_session.query(Notification).filter_by(recipient_admin_id=admin.id).count() == 1
