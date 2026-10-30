"""Section 11 gap: notification preferences (category opt-out + quiet
hours) -- previously no recipient had any way to shape which notifications
they receive; every notify_* call unconditionally created a row."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
import pytest
from sqlalchemy.orm import Session

from app.crud import user_settings

from app.crud import notification as notification_crud
from app.models.notification import Notification
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie


class TestCategoryOptOut:
    def test_opting_out_of_a_category_suppresses_that_notification(self, client, db_session: Session):
        renter = _make_user(db_session, email="notif-pref-optout@test.com")

        r = client.put(
            "/api/users/notifications/preferences",
            json={"optedOutCategories": ["OCCUPANCY"], "quietHoursEnabled": False},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        assert r.json()["optedOutCategories"] == ["OCCUPANCY"]

        notification = notification_crud.notify_user(
            db_session, renter.id, title="Move-in confirmed", message="x",
            notification_type="occupancy.move_in_confirmed",
        )
        assert notification is None
        assert db_session.scalar(select(Notification).where(Notification.recipient_user_id == renter.id)) is None

    def test_a_different_category_still_delivers(self, client, db_session: Session):
        renter = _make_user(db_session, email="notif-pref-othercat@test.com")
        client.put(
            "/api/users/notifications/preferences",
            json={"optedOutCategories": ["OCCUPANCY"], "quietHoursEnabled": False},
            cookies=auth_user_cookie(renter),
        )

        notification = notification_crud.notify_user(
            db_session, renter.id, title="Payment confirmed", message="x", notification_type="payment.confirmed",
        )
        assert notification is not None
        assert notification.category == "PAYMENTS"

    def test_disputes_and_safety_cannot_be_opted_out(self, client, db_session: Session):
        renter = _make_user(db_session, email="notif-pref-safety@test.com")
        r = client.put(
            "/api/users/notifications/preferences",
            json={"optedOutCategories": ["DISPUTES_AND_SAFETY", "PAYMENTS"], "quietHoursEnabled": False},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        assert r.json()["optedOutCategories"] == ["PAYMENTS"]

        notification = notification_crud.notify_user(
            db_session, renter.id, title="Habitability issue", message="x",
            notification_type="habitability_incident.opened",
        )
        assert notification is not None
        assert notification.category == "DISPUTES_AND_SAFETY"


class TestQuietHoursRemoved:
    def test_a_saved_window_holds_nothing_back_any_more(self, client, db_session: Session):
        renter = _make_user(db_session, email="notif-pref-quiet1@test.com")
        now = datetime.now(timezone.utc)
        now_minute = now.hour * 60 + now.minute
        r = client.put(
            "/api/users/notifications/preferences",
            json={
                "optedOutCategories": [], "quietHoursEnabled": True,
                "quietHoursStartMinute": (now_minute - 60) % 1440, "quietHoursEndMinute": (now_minute + 60) % 1440,
            },
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        for kind in ("payment.confirmed", "habitability_incident.opened"):
            assert notification_crud.notify_user(
                db_session, renter.id, title="x", message="x", notification_type=kind,
            ) is not None, kind


class TestPreferencesDefaultsAndAdmin:
    def test_a_user_with_no_preference_row_gets_defaults_and_all_notifications_deliver(self, client, db_session: Session):
        renter = _make_user(db_session, email="notif-pref-defaults@test.com")
        r = client.get("/api/users/notifications/preferences", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["optedOutCategories"] == []
        assert body["quietHoursEnabled"] is False

        notification = notification_crud.notify_user(
            db_session, renter.id, title="Payment confirmed", message="x", notification_type="payment.confirmed",
        )
        assert notification is not None

    def test_admin_preferences_round_trip(self, client, db_session: Session):
        admin = _make_admin(db_session, email="notif-pref-admin@test.com", role="super_admin")
        r = client.put(
            "/api/notifications/preferences",
            json={"optedOutCategories": ["LEASING"], "quietHoursEnabled": False},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["optedOutCategories"] == ["LEASING"]

        notification = notification_crud.notify_admin(
            db_session, admin.id, title="New listing", message="x", notification_type="listing.submitted",
        )
        assert notification is None


class TestCategoryMapping:
    """ZR-61: Tenancy and Rent & payments each switch off exactly their own notices."""

    @pytest.mark.parametrize("notification_type, category", [
        ("rental_payment.due_soon", "PAYMENTS"), ("rental_payment_return.recorded", "PAYMENTS"),
        ("payment_instruction.changed", "PAYMENTS"), ("host_refund.due", "PAYMENTS"), ("listing_fee.paid", "PAYMENTS"),
        ("sublet.payment", "PAYMENTS"), ("sublet_request.payee_setup", "PAYMENTS"),
        ("occupancy.move_out_notice_for_host", "OCCUPANCY"), ("sublet_request.submitted", "OCCUPANCY"),
        ("sublet_message.received", "OCCUPANCY"), ("termination_case.opened", "OCCUPANCY"),
        ("authority.status", "DISPUTES_AND_SAFETY"), ("property_verification.verified", "DISPUTES_AND_SAFETY"),
        ("review.new", "LEASING"),
    ])
    def test_type_lands_in_its_own_category(self, notification_type, category):
        from app.models.notification import category_for_notification_type

        assert category_for_notification_type(notification_type) == category

    def test_related_record_kinds_resolve_for_email(self):
        from app.services.email import recipient as recipient_prefs

        assert recipient_prefs.category_for("rental_payment_obligation", "") == "PAYMENTS"
        assert recipient_prefs.category_for("listing_fee_payment", "") == "PAYMENTS"
        assert recipient_prefs.category_for("occupancy", "") == "OCCUPANCY"

    def test_rent_notice_follows_the_payments_switch_not_tenancy(self, client, db_session: Session):
        renter = _make_user(db_session, email="notif-pref-rent@test.com")
        client.put(
            "/api/users/notifications/preferences",
            json={"optedOutCategories": ["PAYMENTS"], "quietHoursEnabled": False},
            cookies=auth_user_cookie(renter),
        )
        rent = notification_crud.notify_user(
            db_session, renter.id, title="Rent due", message="x", notification_type="rental_payment.due_soon",
            related_entity_type="rental_payment_obligation", related_entity_id="1",
        )
        tenancy = notification_crud.notify_user(
            db_session, renter.id, title="Move out", message="x", notification_type="occupancy.move_out_notice_for_host",
            related_entity_type="occupancy", related_entity_id="1",
        )
        assert rent is None
        assert tenancy is not None and tenancy.category == "OCCUPANCY"


class TestSuperAdminOversightCopy:
    """ZR-61: super admins also get the tenancy, sublet, payment and dispute notices."""

    @pytest.mark.parametrize("kind, entity, label", [
        ("occupancy.move_out_notice_for_host", "occupancy", "Tenancy"),
        ("sublet_request.submitted", "sublet_request", "Tenancy"),
        ("rental_payment.due_soon", "rental_payment_obligation", "Payment"),
        ("host_refund.due", "host_refund", "Payment"),
        ("dispute_case.opened", "dispute_case", "Dispute & safety"),
        ("habitability_incident.opened", "habitability_incident", "Dispute & safety"),
    ])
    def test_user_notice_is_raised_for_every_super_admin(self, db_session: Session, kind, entity, label):
        admin = _make_admin(db_session, email=f"np-over-{label[:3].lower()}@test.com", role="super_admin")
        other = _make_admin(db_session, email=f"np-over-reg-{label[:3].lower()}@test.com", role="admin")
        renter = _make_user(db_session, email=f"np-over-user-{label[:3].lower()}@test.com")
        db_session.commit()
        notification_crud.notify_user(
            db_session, renter.id, title="Heads up", message="details", notification_type=kind,
            related_entity_type=entity, related_entity_id="77",
        )
        mine = db_session.query(Notification).filter_by(recipient_admin_id=admin.id, notification_type=kind).one()
        assert mine.title == f"{label}: Heads up" and renter.email in mine.message
        assert db_session.query(Notification).filter_by(recipient_admin_id=other.id).count() == 0

    def test_private_and_leasing_notices_are_not_copied(self, db_session: Session):
        admin = _make_admin(db_session, email="np-over-skip@test.com", role="super_admin")
        renter = _make_user(db_session, email="np-over-skip-user@test.com")
        db_session.commit()
        for kind in ("identity_verification.verified", "authority.status", "offer.created", "support_ticket.reply"):
            notification_crud.notify_user(db_session, renter.id, title="x", message="x", notification_type=kind,
                                          related_entity_type="x", related_entity_id="1")
        assert db_session.query(Notification).filter_by(recipient_admin_id=admin.id).count() == 0

    def test_admin_category_opt_out_still_applies(self, db_session: Session):
        admin = _make_admin(db_session, email="np-over-optout@test.com", role="super_admin")
        renter = _make_user(db_session, email="np-over-optout-user@test.com")
        notification_crud.update_preference(
            db_session, notification_crud.get_or_create_preference_for_admin(db_session, admin.id),
            opted_out_categories=["PAYMENTS"], quiet_hours_enabled=False, quiet_hours_start_minute=0, quiet_hours_end_minute=0,
        )
        notification_crud.notify_user(db_session, renter.id, title="x", message="x", notification_type="rental_payment.due_soon",
                                      related_entity_type="rental_payment_obligation", related_entity_id="1")
        notification_crud.notify_user(db_session, renter.id, title="x", message="x", notification_type="occupancy.x",
                                      related_entity_type="occupancy", related_entity_id="1")
        kinds = [n.notification_type for n in db_session.query(Notification).filter_by(recipient_admin_id=admin.id)]
        assert kinds == ["occupancy.x"]
