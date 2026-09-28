"""Rent is paid to hosts directly and recording it is optional, so the
platform's recurring work can't wait for a payment first. Covers
services/scheduled_jobs.py (monthly rent creation, due/overdue statuses and
reminders, unconfirmed-payment reminders, booking expiry) and the 7-day
hold a fully signed booking now gets while the host confirms the deposit
and first rent."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud import rental_payment as rp_crud
from app.crud.guest import get_guest_for_user
from app.models.finance import Obligation
from app.models.leasing import Agreement
from app.models.notification import Notification
from app.models.occupancy import Occupancy
from app.models.rental_payment import RentalPaymentObligation
from app.services import scheduled_jobs
from tests.test_booking_change_requests import _signed_agreement_active_occupancy
from tests.test_rental_payment_legacy_bridge import _create_signed_agreement, _rental_payment_obligations_for


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _notifications(db: Session, notification_type: str, entity_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.notification_type == notification_type, Notification.related_entity_id == str(entity_id),
        )
    )


@pytest.mark.payment_boundary
class TestSignedBookingHold:
    def test_a_signed_booking_is_held_for_the_host_to_confirm_payment(self, client, db_session: Session):
        _user, _cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="sj-hold")
        agreement = db_session.get(Agreement, agreement_id)
        assert agreement.status in ("PAYMENT_IN_PROGRESS", "PAYMENT_PENDING")

        expected = datetime.now(timezone.utc) + timedelta(days=settings.direct_payment_confirmation_hold_days)
        deadline = _aware(agreement.payment_session_expires_at)
        assert abs((deadline - expected).total_seconds()) < 120
        # The offer's window is what keeps the room hold alive -- stretched to match.
        assert _aware(agreement.offer.confirmation_expires_at) >= deadline - timedelta(seconds=1)

    def test_the_hourly_expiry_check_leaves_it_alone_within_the_hold(self, client, db_session: Session):
        _user, _cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="sj-hold2")
        scheduled_jobs.expire_overdue_bookings(db_session)
        agreement = db_session.get(Agreement, agreement_id)
        db_session.refresh(agreement)
        assert agreement.status in ("PAYMENT_IN_PROGRESS", "PAYMENT_PENDING")
        assert agreement.offer.status == "ACCEPTED"


class TestMonthlyRentIsCreatedOnSchedule:
    def test_next_month_appears_without_this_month_being_recorded(self, client, db_session: Session):
        agreement_id, _admin_cookies, _renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="sj-rent", start_date=date.today(),
        )
        agreement = db_session.get(Agreement, agreement_id)
        occupancy = db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))
        before = scheduled_jobs._latest_rent_due_date(occupancy)
        assert before is not None and before <= date.today()

        created = scheduled_jobs.generate_due_rent(db_session)
        assert created >= 1
        db_session.expire_all()
        occupancy = db_session.get(Occupancy, occupancy.id)
        after = scheduled_jobs._latest_rent_due_date(occupancy)
        assert after > date.today()
        # The online-rail counterpart the renter and host actually see.
        assert db_session.scalar(select(RentalPaymentObligation).where(
            RentalPaymentObligation.occupancy_id == occupancy.id, RentalPaymentObligation.due_date == after,
        )) is not None

    def test_running_it_again_creates_nothing_more(self, client, db_session: Session):
        agreement_id, _admin_cookies, _renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="sj-rent2", start_date=date.today(),
        )
        scheduled_jobs.generate_due_rent(db_session)
        count = db_session.scalar(select(func.count()).select_from(Obligation).where(Obligation.obligation_type == "RENT"))
        assert scheduled_jobs.generate_due_rent(db_session) == 0
        assert db_session.scalar(
            select(func.count()).select_from(Obligation).where(Obligation.obligation_type == "RENT")
        ) == count


class TestDueStatusesAndReminders:
    def _obligation(self, client, db_session: Session, suffix: str) -> RentalPaymentObligation:
        _user, _cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix=suffix)
        return _rental_payment_obligations_for(db_session, agreement_id)["RENT"]

    def test_past_due_rent_goes_overdue_and_both_sides_are_told_once(self, client, db_session: Session):
        rent = self._obligation(client, db_session, "sj-overdue")
        rent.due_date = date.today() - timedelta(days=2)
        rent.status = "UPCOMING"
        db_session.commit()

        assert scheduled_jobs.refresh_due_statuses(db_session) >= 1
        db_session.refresh(rent)
        assert rent.status == "OVERDUE"
        assert _notifications(db_session, "rental_payment.overdue", rent.id) >= 1

        told = _notifications(db_session, "rental_payment.overdue", rent.id)
        scheduled_jobs.refresh_due_statuses(db_session)
        assert _notifications(db_session, "rental_payment.overdue", rent.id) == told

    def test_the_host_is_reminded_once_about_an_unconfirmed_payment(self, client, db_session: Session):
        _user, _cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="sj-remind")
        rent = _rental_payment_obligations_for(db_session, agreement_id)["RENT"]
        guest = get_guest_for_user(db_session, _user)
        record = rp_crud.mark_paid(
            db_session, guest, rent, amount=float(rent.amount), currency=rent.currency,
            declared_date=date.today(), payment_method_category="UPI",
        )
        record.created_at = datetime.now(timezone.utc) - timedelta(days=settings.payment_confirmation_reminder_days + 1)
        db_session.commit()

        from tests.test_direct_rent_payment_model import _host_user_for

        _host_user_for(db_session, rent.recipient_party_id)  # someone to deliver the reminder to

        assert scheduled_jobs.remind_hosts_of_unconfirmed_payments(db_session) == 1
        assert _notifications(db_session, "rental_payment.confirmation_reminder", record.id) == 1
        assert scheduled_jobs.remind_hosts_of_unconfirmed_payments(db_session) == 0
        assert _notifications(db_session, "rental_payment.confirmation_reminder", record.id) == 1

    def test_a_recent_unconfirmed_payment_is_not_chased_yet(self, client, db_session: Session):
        _user, _cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="sj-recent")
        rent = _rental_payment_obligations_for(db_session, agreement_id)["RENT"]
        guest = get_guest_for_user(db_session, _user)
        rp_crud.mark_paid(
            db_session, guest, rent, amount=float(rent.amount), currency=rent.currency,
            declared_date=date.today(), payment_method_category="BANK_TRANSFER",
        )
        assert scheduled_jobs.remind_hosts_of_unconfirmed_payments(db_session) == 0


class TestRunner:
    def test_every_job_runs_and_one_failure_does_not_stop_the_rest(self, db_session: Session, monkeypatch):
        def boom(_db):
            raise RuntimeError("broken job")

        monkeypatch.setattr(scheduled_jobs, "JOBS", (("broken", boom),) + scheduled_jobs.JOBS)
        results = scheduled_jobs.run_scheduled_jobs(db_session)
        assert results["broken"] == "error"
        assert all(results[name] != "error" for name, _ in scheduled_jobs.JOBS if name != "broken")

    def test_a_super_admin_can_run_the_jobs_now(self, client, db_session: Session):
        from tests.conftest import _make_admin, auth_admin_cookie

        admin = _make_admin(db_session, email="sj-admin@test.com", role="super_admin")
        r = client.post("/api/finance/rental-payments/scheduled-jobs/run", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert set(r.json()) == {name for name, _ in scheduled_jobs.JOBS}
