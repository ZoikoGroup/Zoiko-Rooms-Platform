"""ADD_CO_TENANT as a shared room: the Host's approval puts the co-tenant in the room; pays the renter directly (no agreement or obligations of their own),
sees the renter's tenancy read-only while the room is shared, takes a place
in the room, and any of the three people can end it."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import sublet as sublet_crud
from app.crud.eligibility import check_room_capacity, room_occupant_count
from app.models.finance import Obligation
from app.models.identity_verification import IdentityVerification
from app.models.leasing import Application
from app.models.notification import Notification
from app.models.occupancy import Occupancy
from app.models.sublet_request import SubletRequest
from tests.conftest import _make_user, auth_user_cookie
from tests.test_sublet_arrangement_classification import _host_for, _make_active_tenancy


def _shared_room(db: Session, suffix: str):
    """A two-person room with an ADD_CO_TENANT request awaiting the host.
    Both the renter and the co-tenant are identity-verified."""
    tenant_user, co_tenant, co_tenant_party_id, occupancy_id = _make_active_tenancy(db, suffix=suffix)
    occupancy = db.get(Occupancy, occupancy_id)
    occupancy.room.max_occupants = 2
    db.add(IdentityVerification(party_id=tenant_user.party_id, document_type="passport", document_category="identity", status="verified"))
    db.commit()
    sr = sublet_crud.submit_sublet_request(db, tenant_user, occupancy_id, co_tenant_party_id, "ADD_CO_TENANT")
    return sr, tenant_user, co_tenant, _host_for(db, suffix), occupancy


def _approved_shared_room(client, db: Session, suffix: str):
    sr, tenant_user, co_tenant, host, occupancy = _shared_room(db, suffix)
    r = client.post(f"/api/users/hosting/sublet-requests/{sr.id}/approve", json={}, cookies=auth_user_cookie(host))
    assert r.status_code == 200, r.text
    return sr, tenant_user, co_tenant, host, occupancy, r.json()


def _titles(db: Session, user_id: int) -> list[str]:
    return list(db.scalars(select(Notification.title).where(Notification.recipient_user_id == user_id)))


class TestHostApprovalPutsTheCoTenantInTheRoom:
    def test_the_request_goes_to_the_host_and_the_co_tenant_hears_nothing_yet(self, client, db_session: Session):
        sr, _tenant, co_tenant, host, _occ = _shared_room(db_session, "sr1")
        assert sr.status == "pending_admin_review"
        assert "A sublet was requested for your listing" in _titles(db_session, host.id)
        assert _titles(db_session, co_tenant.id) == []

    def test_the_hosts_approval_is_the_only_step_and_gives_the_co_tenant_one_notification(self, client, db_session: Session):
        sr, tenant_user, co_tenant, _host, occupancy, body = _approved_shared_room(client, db_session, "sr1b")
        assert body["status"] == "approved" and body["sharedRoomActive"] is True
        assert _titles(db_session, co_tenant.id) == ["You're now sharing the room"]
        assert "Your co-tenant was approved" in _titles(db_session, tenant_user.id)
        assert room_occupant_count(db_session, occupancy.room) == 2

    def test_there_is_no_accept_or_decline_step(self, client, db_session: Session):
        sr, _tenant, co_tenant, _host, _occ = _shared_room(db_session, "sr3")
        r = client.post(f"/api/users/rentals/co-tenancies/{sr.id}/respond", json={"accept": True}, cookies=auth_user_cookie(co_tenant))
        assert r.status_code in (404, 405)

    def test_an_unverified_co_tenant_cannot_be_approved(self, client, db_session: Session):
        sr, _tenant, co_tenant, host, _occ = _shared_room(db_session, "sr6")
        for iv in db_session.scalars(select(IdentityVerification).where(IdentityVerification.party_id == co_tenant.party_id)):
            iv.status = "rejected"
        db_session.commit()
        r = client.post(f"/api/users/hosting/sublet-requests/{sr.id}/approve", json={}, cookies=auth_user_cookie(host))
        assert r.status_code == 409
        assert db_session.get(SubletRequest, sr.id).status == "pending_admin_review"

    def test_co_tenant_sees_requests_naming_them(self, client, db_session: Session):
        sr, tenant_user, co_tenant, _host, _occ = _shared_room(db_session, "sr2")
        r = client.get("/api/users/rentals/co-tenancies", cookies=auth_user_cookie(co_tenant))
        assert r.status_code == 200
        body = r.json()
        assert [x["id"] for x in body] == [sr.id]
        assert body[0]["currentTenantIdentityVerified"] is True
        assert body[0]["proposedRenterIdentityVerified"] is True
        # The renter's own list is unchanged; nobody else sees it as "naming them".
        assert client.get("/api/users/rentals/co-tenancies", cookies=auth_user_cookie(tenant_user)).json() == []


class TestApprovedSharedRoom:
    def test_approval_creates_nothing_for_the_co_tenant_to_pay_the_host(self, client, db_session: Session):
        _sr, _tenant, co_tenant, _host, occupancy, body = _approved_shared_room(client, db_session, "sr7")
        assert body["status"] == "approved"
        assert body["sharedRoomActive"] is True
        assert body["newAgreementId"] is None
        assert body["originalRenterLiability"] == "ACTIVE"
        assert body["newOccupantLiability"] == "NONE"
        assert body["payeeModel"] == "EXTERNAL_PAYEE_RECORDED"
        # No application/agreement/obligation was created in the co-tenant's name.
        assert db_session.scalar(select(Application).where(Application.listing_id == occupancy.listing_id, Application.guest_id != occupancy.guest_id)) is None
        assert db_session.scalar(select(Obligation).where(Obligation.agreement_id.is_not(None), Obligation.amount > 0, Obligation.agreement_id != occupancy.offer.agreement.id)) is None

    def test_no_take_over_or_occupant_changed_messages(self, client, db_session: Session):
        _sr, _tenant, co_tenant, host, _occ, _body = _approved_shared_room(client, db_session, "sr8")
        assert "You've been authorized as a new occupant" not in _titles(db_session, co_tenant.id)
        assert "Occupant changed via sublet" not in _titles(db_session, host.id)
        assert "You're now sharing the room" in _titles(db_session, co_tenant.id)

    def test_co_tenant_sees_the_renters_tenancy_read_only(self, client, db_session: Session):
        sr, _tenant, co_tenant, _host, occupancy, _body = _approved_shared_room(client, db_session, "sr9")
        r = client.get(f"/api/users/rentals/co-tenancies/{sr.id}/transaction-record", cookies=auth_user_cookie(co_tenant))
        assert r.status_code == 200, r.text
        assert r.json()["occupancy"]["id"] == occupancy.id
        stranger = _make_user(db_session, email="stranger-sr9@test.com")
        r = client.get(f"/api/users/rentals/co-tenancies/{sr.id}/transaction-record", cookies=auth_user_cookie(stranger))
        assert r.status_code == 403

    def test_the_record_stays_closed_until_the_host_approves(self, client, db_session: Session):
        sr, _tenant, co_tenant, _host, _occ = _shared_room(db_session, "sr10")
        r = client.get(f"/api/users/rentals/co-tenancies/{sr.id}/transaction-record", cookies=auth_user_cookie(co_tenant))
        assert r.status_code == 403

    def test_the_co_tenant_takes_a_place_in_the_room(self, client, db_session: Session):
        _sr, _tenant, _co, _host, occupancy, _body = _approved_shared_room(client, db_session, "sr11")
        assert check_room_capacity(db_session, occupancy.room)  # 1 tenancy + 1 co-tenant = 2/2


class TestEndingTheSharedRoom:
    def test_the_co_tenant_can_leave(self, client, db_session: Session):
        sr, tenant_user, co_tenant, host, occupancy, _body = _approved_shared_room(client, db_session, "sr12")
        r = client.post(f"/api/users/rentals/co-tenancies/{sr.id}/end", json={"reason": "Moving out"}, cookies=auth_user_cookie(co_tenant))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ended"
        assert r.json()["sharedRoomActive"] is False
        assert r.json()["endReason"] == "Moving out"
        assert "Co-tenancy ended" in _titles(db_session, tenant_user.id)
        assert "Co-tenancy ended" in _titles(db_session, host.id)
        # The view closes and the place in the room frees up.
        r = client.get(f"/api/users/rentals/co-tenancies/{sr.id}/transaction-record", cookies=auth_user_cookie(co_tenant))
        assert r.status_code == 403
        assert check_room_capacity(db_session, occupancy.room) == []

    def test_the_renter_and_the_host_can_end_it_but_a_stranger_cannot(self, client, db_session: Session):
        sr, tenant_user, _co, _host, _occ, _body = _approved_shared_room(client, db_session, "sr13")
        stranger = _make_user(db_session, email="stranger-sr13@test.com")
        assert client.post(f"/api/users/rentals/co-tenancies/{sr.id}/end", json={}, cookies=auth_user_cookie(stranger)).status_code == 403
        assert client.post(f"/api/users/rentals/co-tenancies/{sr.id}/end", json={}, cookies=auth_user_cookie(tenant_user)).status_code == 200

        sr2, _t, _c, host2, _o, _b = _approved_shared_room(client, db_session, "sr14")
        r = client.post(f"/api/users/hosting/sublet-requests/{sr2.id}/end-shared-room", json={}, cookies=auth_user_cookie(host2))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ended"

    def test_it_ends_with_the_renters_tenancy(self, client, db_session: Session):
        sr, _tenant, co_tenant, _host, occupancy, _body = _approved_shared_room(client, db_session, "sr15")
        occupancy.status = "ENDED"
        db_session.commit()
        r = client.get("/api/users/rentals/co-tenancies", cookies=auth_user_cookie(co_tenant))
        assert r.json()[0]["sharedRoomActive"] is False
        assert client.get(f"/api/users/rentals/co-tenancies/{sr.id}/transaction-record", cookies=auth_user_cookie(co_tenant)).status_code == 403
