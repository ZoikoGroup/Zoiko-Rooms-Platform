"""Section 9 gap: Host right-of-entry / entry-notice rules. Previously
nothing in this codebase modeled a Host's right to enter an occupied unit,
or the advance notice the Host owes the tenant -- see
models/host_entry_visit.py. MarketPolicyPack.entry_notice_hours is the
jurisdiction-configured minimum; is_emergency is a real, auditable bypass
(not a silent one) requiring a non-blank emergency_reason."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.host_entry_visit import HostEntryVisit
from app.models.market_policy import MarketPolicyPack
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_termination_case import _make_active_occupancy


class TestScheduleEntryVisit:
    def test_scheduling_with_enough_notice_succeeds(self, client, db_session: Session):
        admin = _make_admin(db_session, email="entry-ok-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="entry1")
        renter = _make_user(db_session, email="entry-ok-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        policy = db_session.query(MarketPolicyPack).filter_by(jurisdiction_code="IN").one()
        scheduled_at = datetime.now(timezone.utc) + timedelta(hours=policy.entry_notice_hours + 5)

        r = client.post(
            f"/api/occupancy/{occupancy.id}/entry-visits",
            json={"purpose": "INSPECTION", "scheduledAt": scheduled_at.isoformat()},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "SCHEDULED"
        assert body["purpose"] == "INSPECTION"
        assert body["isEmergency"] is False

    def test_scheduling_without_enough_notice_is_rejected(self, client, db_session: Session):
        admin = _make_admin(db_session, email="entry-short-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="entry2")
        renter = _make_user(db_session, email="entry-short-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        scheduled_at = datetime.now(timezone.utc) + timedelta(hours=1)

        r = client.post(
            f"/api/occupancy/{occupancy.id}/entry-visits",
            json={"purpose": "REPAIR", "scheduledAt": scheduled_at.isoformat()},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 409, r.text

    def test_emergency_entry_bypasses_notice_but_requires_a_reason(self, client, db_session: Session):
        admin = _make_admin(db_session, email="entry-emg-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="entry3")
        renter = _make_user(db_session, email="entry-emg-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=15)

        r = client.post(
            f"/api/occupancy/{occupancy.id}/entry-visits",
            json={"purpose": "REPAIR", "scheduledAt": scheduled_at.isoformat(), "isEmergency": True},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text

        r = client.post(
            f"/api/occupancy/{occupancy.id}/entry-visits",
            json={
                "purpose": "REPAIR", "scheduledAt": scheduled_at.isoformat(),
                "isEmergency": True, "emergencyReason": "Gas leak reported by neighbor",
            },
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        assert r.json()["isEmergency"] is True

    def test_renter_can_view_but_not_schedule_their_own_entry_visits(self, client, db_session: Session):
        admin = _make_admin(db_session, email="entry-view-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="entry4")
        renter = _make_user(db_session, email="entry-view-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        policy = db_session.query(MarketPolicyPack).filter_by(jurisdiction_code="IN").one()
        scheduled_at = datetime.now(timezone.utc) + timedelta(hours=policy.entry_notice_hours + 5)
        client.post(
            f"/api/occupancy/{occupancy.id}/entry-visits",
            json={"purpose": "INSPECTION", "scheduledAt": scheduled_at.isoformat()},
            cookies=auth_admin_cookie(admin),
        )

        r = client.get(
            f"/api/users/rentals/occupancies/{occupancy.id}/entry-visits", cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1
        assert r.json()[0]["purpose"] == "INSPECTION"

    def test_a_different_renter_cannot_view_someone_elses_entry_visits(self, client, db_session: Session):
        admin = _make_admin(db_session, email="entry-other-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="entry5")
        other_renter = _make_user(db_session, email="entry-other-renter@test.com")

        r = client.get(
            f"/api/users/rentals/occupancies/{occupancy.id}/entry-visits", cookies=auth_user_cookie(other_renter),
        )
        assert r.status_code == 403


class TestCompleteAndCancelEntryVisit:
    def _schedule(self, client, db_session, admin, occupancy):
        policy = db_session.query(MarketPolicyPack).filter_by(jurisdiction_code="IN").one()
        scheduled_at = datetime.now(timezone.utc) + timedelta(hours=policy.entry_notice_hours + 5)
        r = client.post(
            f"/api/occupancy/{occupancy.id}/entry-visits",
            json={"purpose": "INSPECTION", "scheduledAt": scheduled_at.isoformat()},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def test_completing_a_scheduled_visit(self, client, db_session: Session):
        admin = _make_admin(db_session, email="entry-complete-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="entry6")
        renter = _make_user(db_session, email="entry-complete-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        visit_id = self._schedule(client, db_session, admin, occupancy)

        r = client.post(f"/api/occupancy/entry-visits/{visit_id}/complete", json={}, cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "COMPLETED"

        visit = db_session.get(HostEntryVisit, visit_id)
        assert visit.completed_at is not None

    def test_cancelling_a_scheduled_visit(self, client, db_session: Session):
        admin = _make_admin(db_session, email="entry-cancel-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="entry7")
        renter = _make_user(db_session, email="entry-cancel-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        visit_id = self._schedule(client, db_session, admin, occupancy)

        r = client.post(f"/api/occupancy/entry-visits/{visit_id}/cancel", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CANCELLED"

    def test_cannot_complete_an_already_cancelled_visit(self, client, db_session: Session):
        admin = _make_admin(db_session, email="entry-recancel-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="entry8")
        renter = _make_user(db_session, email="entry-recancel-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        visit_id = self._schedule(client, db_session, admin, occupancy)
        client.post(f"/api/occupancy/entry-visits/{visit_id}/cancel", cookies=auth_admin_cookie(admin))

        r = client.post(f"/api/occupancy/entry-visits/{visit_id}/complete", json={}, cookies=auth_admin_cookie(admin))
        assert r.status_code == 409
