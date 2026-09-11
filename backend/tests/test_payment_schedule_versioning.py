"""ZR-ENG-CLR-005 Section 7.2: a rent-changing agreement amendment reaching
EFFECTIVE supersedes the agreement's PaymentSchedule with a new ACTIVE
version, without rewriting any already-generated Obligation. Reuses the real
end-to-end amendment flow already proven in
test_agreement_engine_extensions_2.py::test_full_amendment_lifecycle_reaches_effective,
extended with schedule/obligation assertions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import occupancy as occupancy_crud
from app.models.finance import PaymentSchedule
from app.models.leasing import Agreement
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_agreement_engine_extensions_2 import _full_signed_agreement
from tests.test_room_hold_atomicity import _make_listing_with_room, _make_verified_renter


class TestRentChangingAmendmentSupersedesSchedule:
    def test_amendment_reaching_effective_creates_a_new_active_schedule_version(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="pschedver-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="pschedver-renter1@test.com")

        agreement_id = _full_signed_agreement(client, db_session, admin_cookies, renter, listing_id)

        original_schedule = db_session.scalar(
            select(PaymentSchedule).where(PaymentSchedule.agreement_id == agreement_id, PaymentSchedule.status == "ACTIVE")
        )
        assert original_schedule is not None
        assert float(original_schedule.amount) == 500.0
        assert original_schedule.version == 1
        original_schedule_id = original_schedule.id

        r = client.post(f"/api/leasing/agreements/{agreement_id}/amendments", json={"reason": "Rent review"}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        amendment_id = r.json()["id"]
        client.post(
            f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/classify",
            json={"amendmentType": "MATERIAL_CHANGE"}, cookies=admin_cookies,
        )
        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/propose-terms",
            json={"proposedTerms": {"monthlyRent": 750}}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        r = client.post(f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/approve", cookies=admin_cookies)
        assert r.status_code == 200, r.text

        # Not yet effective -- still re-signing. Schedule must be untouched
        # until the amendment actually reaches EFFECTIVE.
        db_session.commit()
        still_active = db_session.scalar(
            select(PaymentSchedule).where(PaymentSchedule.agreement_id == agreement_id, PaymentSchedule.status == "ACTIVE")
        )
        assert still_active.id == original_schedule_id

        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        r = client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SIGNED"

        db_session.refresh(original_schedule)
        assert original_schedule.status == "SUPERSEDED"

        new_schedule = db_session.scalar(
            select(PaymentSchedule).where(PaymentSchedule.agreement_id == agreement_id, PaymentSchedule.status == "ACTIVE")
        )
        assert new_schedule is not None
        assert new_schedule.id != original_schedule_id
        assert new_schedule.version == 2
        assert float(new_schedule.amount) == 750.0

        # The original obligation (already paid before the amendment) is untouched.
        agreement = db_session.get(Agreement, agreement_id)
        original_rent_obligation = next(o for o in agreement.obligations if o.obligation_type == "RENT")
        assert float(original_rent_obligation.amount) == 500.0
        assert original_rent_obligation.status == "PAID"

        # A real move-in + next-rent-generation picks up the NEW amount.
        agreement = db_session.get(Agreement, agreement_id)
        occupancy = occupancy_crud.confirm_move_in(db_session, agreement, super_admin)
        next_obligation = occupancy_crud.generate_next_rent_obligation(db_session, occupancy, super_admin)
        assert next_obligation is not None
        assert next_obligation.schedule_id == new_schedule.id
        assert float(next_obligation.amount) == 750.0

    def test_amendment_without_a_rent_change_leaves_the_schedule_alone(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="pschedver-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="pschedver-renter2@test.com")

        agreement_id = _full_signed_agreement(client, db_session, admin_cookies, renter, listing_id)
        original_schedule = db_session.scalar(
            select(PaymentSchedule).where(PaymentSchedule.agreement_id == agreement_id, PaymentSchedule.status == "ACTIVE")
        )
        original_schedule_id = original_schedule.id

        r = client.post(f"/api/leasing/agreements/{agreement_id}/amendments", json={"reason": "Fix a typo"}, cookies=admin_cookies)
        amendment_id = r.json()["id"]
        client.post(
            f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/classify",
            json={"amendmentType": "CORRECTION"}, cookies=admin_cookies,
        )
        # No monthlyRent key -- only depositAmount changes.
        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/propose-terms",
            json={"proposedTerms": {"depositAmount": 600}}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        client.post(f"/api/leasing/agreements/{agreement_id}/amendments/{amendment_id}/approve", cookies=admin_cookies)

        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        r = client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
        assert r.status_code == 200, r.text

        schedules = db_session.scalars(select(PaymentSchedule).where(PaymentSchedule.agreement_id == agreement_id)).all()
        assert len(schedules) == 1
        assert schedules[0].id == original_schedule_id
        assert schedules[0].status == "ACTIVE"
        assert schedules[0].version == 1
