"""Host Rental Records list (crud/host_rental_records.py) and the host's own
move-out confirmation."""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.leasing import Offer
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.occupancy_activation import OccupancyHandoverEvent
from tests.conftest import _make_user, auth_user_cookie
from tests.test_host_offer_agreement_decisions import _make_agreement_eligible, _submit_and_approve_application
from tests.test_self_listing_restriction import _make_host_and_listing, _make_verified_renter


def _agreement_sent(client, db_session: Session, suffix: str):
    host, listing_id = _make_host_and_listing(db_session, email=f"rr-host-{suffix}@test.com")
    renter = _make_verified_renter(db_session, email=f"rr-renter-{suffix}@test.com")
    host_cookies, renter_cookies = auth_user_cookie(host), auth_user_cookie(renter)
    application_id = _submit_and_approve_application(client, host, renter, listing_id)

    offer_id = client.post(f"/api/users/hosting/applications/{application_id}/offers", cookies=host_cookies).json()["id"]
    start = date.today() + timedelta(days=5)
    r = client.post(
        f"/api/users/hosting/offers/{offer_id}/terms",
        json={"monthlyRent": 640, "depositAmount": 900, "startDate": start.isoformat(), "termMonths": 6},
        cookies=host_cookies,
    )
    assert r.status_code == 200, r.text
    assert client.post(f"/api/users/hosting/offers/{offer_id}/send", cookies=host_cookies).status_code == 200
    assert client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=renter_cookies).status_code == 200
    _make_agreement_eligible(db_session, listing_id)
    agreement_id = client.post(f"/api/users/hosting/offers/{offer_id}/agreement", cookies=host_cookies).json()["id"]
    assert client.post(f"/api/users/hosting/agreements/{agreement_id}/send", cookies=host_cookies).status_code == 200
    return host, renter, listing_id, application_id, offer_id, agreement_id, start


class TestHostRentalRecords:
    def test_agreement_stage_record_uses_real_terms_and_asks_host_to_sign(self, client, db_session: Session):
        host, _renter, _listing_id, application_id, offer_id, agreement_id, start = _agreement_sent(client, db_session, "a")

        r = client.get("/api/users/hosting/rental-records", cookies=auth_user_cookie(host))
        assert r.status_code == 200, r.text
        [rec] = r.json()
        assert rec["ref"] == f"ZR-A-{application_id}"
        assert rec["occupancyId"] is None and rec["offerId"] == offer_id and rec["agreementId"] == agreement_id
        assert rec["status"] == "pending"
        assert rec["nextAction"] == "SIGN_AGREEMENT"
        assert rec["monthlyRent"] == 640 and rec["depositAmount"] == 900 and rec["termMonths"] == 6
        assert rec["startDate"] == start.isoformat()
        assert rec["endDate"] > rec["startDate"]
        assert rec["renters"] and rec["renters"][0]

    def test_active_tenancy_with_move_out_notice_then_host_confirms(self, client, db_session: Session):
        host, renter, listing_id, _app_id, offer_id, _agreement_id, start = _agreement_sent(client, db_session, "b")
        offer = db_session.get(Offer, offer_id)
        listing = db_session.get(Listing, listing_id)
        occ = Occupancy(
            offer_id=offer_id, listing_id=listing_id, room_id=listing.room_id, guest_id=offer.guest_id,
            status="ACTIVE", move_in_date=start, expected_end_date=start + timedelta(days=180),
        )
        db_session.add(occ)
        db_session.commit()

        [rec] = client.get("/api/users/hosting/rental-records", cookies=auth_user_cookie(host)).json()
        assert rec["ref"] == f"ZR-R-{occ.id}" and rec["status"] == "active"

        r = client.post(f"/api/users/rentals/occupancies/{occ.id}/move-out/notice", json={"notes": "Leaving"}, cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        [rec] = client.get("/api/users/hosting/rental-records", cookies=auth_user_cookie(host)).json()
        assert rec["status"] == "notice"
        assert rec["notice"]["kind"] == "MOVE_OUT_NOTICE" and rec["notice"]["initiatedBy"] == "RENTER"
        assert rec["nextAction"] == "REVIEW_NOTICE"

        r = client.post(f"/api/users/hosting/occupancies/{occ.id}/move-out/confirm", json={}, cookies=auth_user_cookie(host))
        assert r.status_code == 200, r.text
        assert db_session.query(OccupancyHandoverEvent).filter_by(
            occupancy_id=occ.id, event_type="HOST_MOVE_OUT_CONFIRMED", actor_kind="provider_user",
        ).count() == 1
        [rec] = client.get("/api/users/hosting/rental-records", cookies=auth_user_cookie(host)).json()
        assert rec["notice"]["status"] == "HOST_CONFIRMED"

    def test_other_users_see_nothing_and_cannot_confirm(self, client, db_session: Session):
        _host, _renter, listing_id, _app_id, offer_id, _agr, start = _agreement_sent(client, db_session, "c")
        stranger = _make_user(db_session, email="rr-stranger-c@test.com")
        offer = db_session.get(Offer, offer_id)
        occ = Occupancy(
            offer_id=offer_id, listing_id=listing_id, room_id=db_session.get(Listing, listing_id).room_id,
            guest_id=offer.guest_id, status="ACTIVE", move_in_date=start,
        )
        db_session.add(occ)
        db_session.commit()
        assert client.get("/api/users/hosting/rental-records", cookies=auth_user_cookie(stranger)).json() == []
        r = client.post(f"/api/users/hosting/occupancies/{occ.id}/move-out/confirm", json={}, cookies=auth_user_cookie(stranger))
        assert r.status_code == 403
