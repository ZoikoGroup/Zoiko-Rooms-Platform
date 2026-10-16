"""The host's own tenancy decisions (routes/host_tenancy.py): renter change
requests, host-proposed amendments, and ending a tenancy -- all previously
admin-portal only."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.agreement_amendment import AgreementAmendment
from app.models.leasing import Agreement
from app.models.occupancy import Occupancy
from tests.conftest import _make_user, auth_user_cookie
from tests.test_booking_change_requests import _signed_agreement_active_occupancy


def _host_for_agreement(db_session: Session, agreement_id: int, email: str):
    """A self-service host user on the agreement's property-owning party."""
    agreement = db_session.get(Agreement, agreement_id)
    party_id = agreement.offer.listing.room.property.owner_party_id
    host = _make_user(db_session, email=email)
    host.party_id = party_id
    db_session.commit()
    return host


def _setup(client, db_session: Session, suffix: str):
    agreement_id, _admin_cookies, renter = _signed_agreement_active_occupancy(
        client, db_session, email_suffix=f"ht{suffix}", start_date=date.today() - timedelta(days=10),
    )
    host = _host_for_agreement(db_session, agreement_id, f"ht-host-{suffix}@test.com")
    return agreement_id, host, renter


class TestHostChangeRequests:
    def test_host_approves_a_renter_extension_and_both_must_re_sign(self, client, db_session: Session):
        agreement_id, host, renter = _setup(client, db_session, "a")
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/extension-requests",
            json={"additionalTermMonths": 3, "reason": "staying on"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        bcr_id = r.json()["id"]

        listed = client.get("/api/users/hosting/change-requests", cookies=auth_user_cookie(host)).json()
        assert [b["id"] for b in listed] == [bcr_id]

        r = client.post(f"/api/users/hosting/change-requests/{bcr_id}/approve", json={"decisionNote": "ok"}, cookies=auth_user_cookie(host))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "AWAITING_AGREEMENT_ACTION"
        assert r.json()["decidedByUserId"] == host.id and r.json()["decidedByAdminId"] is None

        db_session.expire_all()
        agreement = db_session.get(Agreement, agreement_id)
        assert agreement.status == "AMENDMENT_PENDING"
        amendment = db_session.get(AgreementAmendment, r.json()["resultingAmendmentId"])
        assert amendment.requested_by_user_id == host.id and amendment.requested_by_admin_id is None

    def test_host_declines_and_strangers_cannot_decide(self, client, db_session: Session):
        agreement_id, host, renter = _setup(client, db_session, "b")
        bcr_id = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/extension-requests",
            json={"additionalTermMonths": 2}, cookies=auth_user_cookie(renter),
        ).json()["id"]
        stranger = _make_user(db_session, email="ht-stranger-b@test.com")
        stranger.party_id = None
        db_session.commit()
        r = client.post(f"/api/users/hosting/change-requests/{bcr_id}/approve", json={}, cookies=auth_user_cookie(stranger))
        assert r.status_code == 403
        assert client.get("/api/users/hosting/change-requests", cookies=auth_user_cookie(stranger)).json() == []

        r = client.post(f"/api/users/hosting/change-requests/{bcr_id}/decline", json={"decisionNote": "not now"}, cookies=auth_user_cookie(host))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "REJECTED"
        db_session.expire_all()
        assert db_session.get(Agreement, agreement_id).status == "SIGNED"


class TestHostAmendments:
    def test_host_proposes_rent_change_and_extension(self, client, db_session: Session):
        agreement_id, host, _renter = _setup(client, db_session, "c")
        r = client.post(
            f"/api/users/hosting/agreements/{agreement_id}/amendments",
            json={"monthlyRent": 777, "additionalTermMonths": 2, "reason": "Annual review"},
            cookies=auth_user_cookie(host),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "EXECUTION_PENDING" and body["requestedByUserId"] == host.id
        assert body["proposedTerms"]["monthlyRent"] == 777

        db_session.expire_all()
        agreement = db_session.get(Agreement, agreement_id)
        assert agreement.status == "AMENDMENT_PENDING"
        assert float(agreement.offer.terms[-1].monthly_rent) == 777

        # A second proposal while the first awaits signatures is refused.
        r = client.post(f"/api/users/hosting/agreements/{agreement_id}/amendments", json={"monthlyRent": 800}, cookies=auth_user_cookie(host))
        assert r.status_code == 409

        listed = client.get(f"/api/users/hosting/agreements/{agreement_id}/amendments", cookies=auth_user_cookie(host)).json()
        assert len(listed) == 1

    def test_amendment_needs_a_change_and_ownership(self, client, db_session: Session):
        agreement_id, host, _renter = _setup(client, db_session, "d")
        assert client.post(f"/api/users/hosting/agreements/{agreement_id}/amendments", json={}, cookies=auth_user_cookie(host)).status_code == 400
        stranger = _make_user(db_session, email="ht-stranger-d@test.com")
        db_session.commit()
        r = client.post(f"/api/users/hosting/agreements/{agreement_id}/amendments", json={"monthlyRent": 1}, cookies=auth_user_cookie(stranger))
        assert r.status_code == 404


class TestHostTermination:
    def _occupancy_id(self, db_session: Session, agreement_id: int) -> int:
        agreement = db_session.get(Agreement, agreement_id)
        return db_session.query(Occupancy).filter(Occupancy.offer_id == agreement.offer_id).one().id

    def test_host_proposes_surrender_and_renter_accepts(self, client, db_session: Session):
        agreement_id, host, renter = _setup(client, db_session, "e")
        occ_id = self._occupancy_id(db_session, agreement_id)
        proposed = (date.today() + timedelta(days=40)).isoformat()
        r = client.post(
            f"/api/users/hosting/occupancies/{occ_id}/termination-cases",
            json={"causeCode": "MUTUAL_SURRENDER", "proposedEffectiveDate": proposed}, cookies=auth_user_cookie(host),
        )
        assert r.status_code == 201, r.text
        case = r.json()
        assert case["status"] == "SURRENDER_PROPOSED" and case["initiatorHostUserId"] == host.id

        # The host can't accept their own proposal; the renter can.
        assert client.post(f"/api/users/hosting/termination-cases/{case['id']}/accept-surrender", cookies=auth_user_cookie(host)).status_code == 409
        r = client.post(f"/api/users/rentals/termination-cases/{case['id']}/accept-surrender", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "EFFECTIVE_DATE_SET"

        # Rental Records now shows the notice.
        [rec] = client.get("/api/users/hosting/rental-records", cookies=auth_user_cookie(host)).json()
        assert rec["status"] == "notice" and rec["notice"]["initiatedBy"] == "HOST"

    def test_renter_proposes_surrender_and_host_accepts(self, client, db_session: Session):
        agreement_id, host, renter = _setup(client, db_session, "f")
        occ_id = self._occupancy_id(db_session, agreement_id)
        proposed = (date.today() + timedelta(days=40)).isoformat()
        case_id = client.post(
            f"/api/users/rentals/occupancies/{occ_id}/termination-cases",
            json={"causeCode": "MUTUAL_SURRENDER", "proposedEffectiveDate": proposed}, cookies=auth_user_cookie(renter),
        ).json()["id"]

        [rec] = client.get("/api/users/hosting/rental-records", cookies=auth_user_cookie(host)).json()
        assert rec["nextAction"] == "RESPOND_SURRENDER"

        r = client.post(f"/api/users/hosting/termination-cases/{case_id}/accept-surrender", cookies=auth_user_cookie(host))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "EFFECTIVE_DATE_SET"

    def test_host_cannot_use_a_renter_only_cause(self, client, db_session: Session):
        agreement_id, host, _renter = _setup(client, db_session, "g")
        occ_id = self._occupancy_id(db_session, agreement_id)
        r = client.post(
            f"/api/users/hosting/occupancies/{occ_id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(host),
        )
        assert r.status_code == 400
