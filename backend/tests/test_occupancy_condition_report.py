"""Section 9 gap: "No move-in/move-out condition report (photos, damage
notes, etc.) anywhere" -- app/models/occupancy_condition_report.py,
app/crud/occupancy_condition_report.py and the /condition-report routes
added to app/api/routes/occupancy.py and app/api/routes/user_rentals.py."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from tests.conftest import auth_user_cookie
from tests.test_booking_change_requests import _signed_agreement_active_occupancy

_PNG_BYTES = b"\x89PNG\r\n\x1a\n fake png bytes for testing"


def _occupancy_for(db_session: Session, agreement_id: int):
    from sqlalchemy import select
    from app.models.leasing import Agreement
    from app.models.occupancy import Occupancy

    agreement = db_session.get(Agreement, agreement_id)
    return db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))


class TestRenterConditionReport:
    def test_renter_adds_a_photo_item(self, client, db_session: Session):
        agreement_id, _admin_cookies, renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="cr1", start_date=date.today() - timedelta(days=100),
        )
        occupancy = _occupancy_for(db_session, agreement_id)

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/condition-report",
            data={"report_type": "MOVE_OUT", "area": "Bedroom", "condition_rating": "FAIR", "notes": "Small scuff on wall"},
            files={"file": ("wall.png", _PNG_BYTES, "image/png")},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["reportType"] == "MOVE_OUT"
        assert body["area"] == "Bedroom"
        assert body["conditionRating"] == "FAIR"
        assert body["hasFile"] is True

    def test_renter_adds_a_notes_only_item(self, client, db_session: Session):
        agreement_id, _admin_cookies, renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="cr2", start_date=date.today() - timedelta(days=100),
        )
        occupancy = _occupancy_for(db_session, agreement_id)

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/condition-report",
            data={"report_type": "MOVE_IN", "area": "Kitchen", "notes": "Everything looked clean at move-in"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        assert r.json()["hasFile"] is False

    def test_empty_item_is_rejected(self, client, db_session: Session):
        agreement_id, _admin_cookies, renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="cr3", start_date=date.today() - timedelta(days=100),
        )
        occupancy = _occupancy_for(db_session, agreement_id)

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/condition-report",
            data={"report_type": "MOVE_IN"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 400, r.text

    def test_a_different_renter_cannot_add_to_someone_elses_occupancy(self, client, db_session: Session):
        from tests.conftest import _make_user

        agreement_id, _admin_cookies, _renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="cr4", start_date=date.today() - timedelta(days=100),
        )
        occupancy = _occupancy_for(db_session, agreement_id)
        intruder = _make_user(db_session, email="cr4-intruder@test.com")

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/condition-report",
            data={"report_type": "MOVE_IN", "notes": "not mine"},
            cookies=auth_user_cookie(intruder),
        )
        assert r.status_code == 403, r.text

    def test_both_renter_and_host_items_appear_in_the_same_report(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="cr5", start_date=date.today() - timedelta(days=100),
        )
        occupancy = _occupancy_for(db_session, agreement_id)

        client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/condition-report",
            data={"report_type": "MOVE_OUT", "notes": "Renter's own note"},
            cookies=auth_user_cookie(renter),
        )
        client.post(
            f"/api/occupancy/{occupancy.id}/condition-report",
            data={"report_type": "MOVE_OUT", "notes": "Host's own note"},
            cookies=admin_cookies,
        )

        r = client.get(f"/api/users/rentals/occupancies/{occupancy.id}/condition-report", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        notes = {item["notes"] for item in r.json()}
        assert notes == {"Renter's own note", "Host's own note"}

        r = client.get(f"/api/occupancy/{occupancy.id}/condition-report", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert len(r.json()) == 2

    def test_report_type_filters_correctly(self, client, db_session: Session):
        agreement_id, _admin_cookies, renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="cr6", start_date=date.today() - timedelta(days=100),
        )
        occupancy = _occupancy_for(db_session, agreement_id)

        client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/condition-report",
            data={"report_type": "MOVE_IN", "notes": "in"}, cookies=auth_user_cookie(renter),
        )
        client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/condition-report",
            data={"report_type": "MOVE_OUT", "notes": "out"}, cookies=auth_user_cookie(renter),
        )

        r = client.get(
            f"/api/users/rentals/occupancies/{occupancy.id}/condition-report",
            params={"report_type": "MOVE_IN"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1
        assert r.json()[0]["notes"] == "in"
