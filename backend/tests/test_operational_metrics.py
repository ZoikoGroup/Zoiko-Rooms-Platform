"""ZR-ENG-CLR-001 Section 15 operational metrics
(app/services/operational_metrics.py, GET /api/analytics/section1-operational-metrics).
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.leasing import Offer
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_room_hold_atomicity import _apply_and_send_offer, _make_listing_with_room, _make_verified_renter


class TestSection1OperationalMetricsAuthz:
    def test_plain_admin_cannot_view_metrics(self, client, db_session: Session):
        admin = _make_admin(db_session, email="metrics-plain@test.com", role="admin")
        r = client.get("/api/analytics/section1-operational-metrics", cookies=auth_admin_cookie(admin))
        assert r.status_code == 403, r.text


class TestSection1OperationalMetricsValues:
    def test_empty_platform_reports_nulls_and_zero_counts(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="metrics-super1@test.com", role="super_admin")
        r = client.get("/api/analytics/section1-operational-metrics", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["approvalTurnaroundSecondsAvg"] is None
        assert body["publicationFailureRate"] is None
        assert body["holdConversionRate"] is None
        assert body["holdExpiryRate"] is None
        assert body["staleHoldCount"] == 0
        assert body["duplicateConfirmationIncidents"] == 0
        assert body["manualOverrideFrequency"] == 0

    def test_hold_conversion_rate_reflects_a_completed_agreement(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="metrics-super2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="metrics-renter2@test.com")

        _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
        client.post(
            f"/api/leasing/offers/{offer_id}/terms",
            json={"monthlyRent": 500, "depositAmount": 500, "startDate": (date.today() + timedelta(days=5)).isoformat(), "termMonths": 6},
            cookies=admin_cookies,
        )
        r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text

        from app.models.authority_record import AuthorityRecord
        from app.models.listing import Listing
        from app.models.market_release import MarketRelease
        from app.models.occupancy_classification import OccupancyClassification
        from app.models.room import Room

        offer = db_session.get(Offer, offer_id)
        listing = db_session.get(Listing, listing_id)
        room = db_session.get(Room, offer.listing.room_id)
        release = MarketRelease(jurisdiction="England", status="active")
        db_session.add(release)
        db_session.flush()
        listing.market_release_id = release.id
        db_session.add(AuthorityRecord(party_id=listing.party_id or 1, room_id=room.id, authority_type="lease_agreement", status="verified"))
        db_session.add(OccupancyClassification(room_id=room.id, classification="shared_residential_room", review_state="APPROVED"))
        db_session.commit()

        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        assert r.status_code == 200, r.text

        r = client.get("/api/analytics/section1-operational-metrics", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["holdConversionRate"] == 1.0
