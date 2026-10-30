"""A shared-room co-tenant (ADD_CO_TENANT): their monthly rent share to the renter is recorded as a payment
the co-tenant marks paid and the renter confirms, and they have a private thread with the host."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import rental_payment as rp_crud
from app.crud import sublet as sublet_crud
from app.crud.guest import get_guest_for_user
from app.models.identity_verification import IdentityVerification
from app.models.notification import Notification
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.rental_message import SubletMessage
from app.models.rental_payment import RentalPaymentObligation
from app.models.sublet_request import SubletRequest
from app.services.shared_room_payments import sweep_shared_room_rent, sync_shared_room_rent
from tests.conftest import _make_user, auth_user_cookie
from tests.test_sublet_arrangement_classification import _host_for, _make_active_tenancy


def _shared_room(db: Session, suffix: str, *, rent: float | None = 300):
    tenant_user, co_tenant, co_party_id, occupancy_id = _make_active_tenancy(db, suffix=suffix)
    occupancy = db.get(Occupancy, occupancy_id)
    occupancy.room.max_occupants = 2
    db.add(IdentityVerification(party_id=tenant_user.party_id, document_type="passport", document_category="identity", status="verified"))
    db.commit()
    sr = sublet_crud.submit_sublet_request(
        db, tenant_user, occupancy_id, co_party_id, "ADD_CO_TENANT", proposed_monthly_rent=rent,
    )
    return sr, tenant_user, co_tenant, _host_for(db, suffix), occupancy


def _approved(client, db: Session, suffix: str, *, rent: float | None = 300):
    sr, tenant_user, co_tenant, host, occupancy = _shared_room(db, suffix, rent=rent)
    r = client.post(f"/api/users/hosting/sublet-requests/{sr.id}/approve", json={}, cookies=auth_user_cookie(host))
    assert r.status_code == 200, r.text
    return db.get(SubletRequest, sr.id), tenant_user, co_tenant, host, occupancy


def _rent(db: Session, sr: SubletRequest) -> list[RentalPaymentObligation]:
    return list(db.scalars(select(RentalPaymentObligation).where(
        RentalPaymentObligation.sublet_request_id == sr.id).order_by(RentalPaymentObligation.due_date)))


class TestCoTenantRentShare:
    def test_approval_records_the_share_the_co_tenant_owes_the_renter(self, client, db_session: Session):
        sr, tenant_user, co_tenant, _host, _occ = _approved(client, db_session, "pay1")
        [rent] = _rent(db_session, sr)
        assert float(rent.amount) == 300.0 and rent.obligation_type == "RENT"
        assert rent.tenant_guest_id == get_guest_for_user(db_session, co_tenant).id
        assert rent.recipient_party_id == tenant_user.party_id  # the renter, not the host
        assert rent.agreement_id is None and rent.occupancy_id is None
        assert rent.payee_type == "SUBLESSOR" and rent.context_label.startswith("Shared room")
        # Both sides can see it in their own payments list.
        assert rent in rp_crud.list_obligations_for_tenant(db_session, rent.tenant_guest_id)
        assert rent in rp_crud.list_obligations_for_recipient(db_session, tenant_user.party_id)

    def test_nothing_is_recorded_when_no_share_was_agreed(self, client, db_session: Session):
        sr, _t, _c, _h, _o = _approved(client, db_session, "pay2", rent=None)
        assert _rent(db_session, sr) == []

    def test_the_co_tenant_marks_it_paid_and_the_renter_confirms(self, client, db_session: Session):
        sr, _tenant, co_tenant, _host, _occ = _approved(client, db_session, "pay3")
        [rent] = _rent(db_session, sr)
        guest = get_guest_for_user(db_session, co_tenant)
        record = rp_crud.mark_paid(
            db_session, guest, rent, amount=300, currency=rent.currency, declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        assert record.status == "PAYER_RECORDED"
        rp_crud.confirm_receipt(db_session, db_session.get(Party, rent.recipient_party_id), record)
        db_session.refresh(rent)
        assert rent.status == "CONFIRMED"

    def test_each_month_is_added_once_and_only_when_it_is_near(self, client, db_session: Session):
        sr, _t, _c, _h, _occ = _approved(client, db_session, "pay4")
        assert len(_rent(db_session, sr)) == 1  # this month, at approval
        # Next month falls due 31 days out, so it is not within the 30-day lead on day 0...
        assert sync_shared_room_rent(db_session, sr, today=date.today()) == 0
        # ...but is once ten days have passed -- recorded once, however often the job runs.
        assert sync_shared_room_rent(db_session, sr, today=date.today() + timedelta(days=10)) == 1
        assert sync_shared_room_rent(db_session, sr, today=date.today() + timedelta(days=10)) == 0
        assert len(_rent(db_session, sr)) == 2
        # The tenancy ends in 60 days, so a month falling due after that is never recorded.
        assert sync_shared_room_rent(db_session, sr, today=date.today() + timedelta(days=40)) == 0

    def test_rent_stops_at_the_end_of_the_tenancy(self, client, db_session: Session):
        sr, _t, _c, _h, occupancy = _approved(client, db_session, "pay5")
        occupancy.expected_end_date = date.today() + timedelta(days=20)
        db_session.commit()
        assert sync_shared_room_rent(db_session, sr, today=date.today() + timedelta(days=200)) == 0

    def test_the_daily_job_keeps_every_active_shared_room_up_to_date(self, client, db_session: Session):
        sr, _t, _c, _h, _o = _approved(client, db_session, "pay6")
        assert sweep_shared_room_rent(db_session) == 0  # already recorded at approval
        db_session.query(RentalPaymentObligation).filter_by(sublet_request_id=sr.id).delete()
        db_session.commit()
        assert sweep_shared_room_rent(db_session) == 1 and sweep_shared_room_rent(db_session) == 0

    def test_ending_the_co_tenancy_cancels_rent_not_yet_started_but_keeps_what_was_paid(self, client, db_session: Session):
        sr, tenant_user, co_tenant, _host, _occ = _approved(client, db_session, "pay7")
        sync_shared_room_rent(db_session, sr, today=date.today() + timedelta(days=10))
        first, second = _rent(db_session, sr)
        guest = get_guest_for_user(db_session, co_tenant)
        rp_crud.mark_paid(db_session, guest, first, amount=300, currency=first.currency, declared_date=date.today(), payment_method_category="BANK_TRANSFER")

        r = client.post(f"/api/users/rentals/co-tenancies/{sr.id}/end", json={"reason": "moving"}, cookies=auth_user_cookie(tenant_user))
        assert r.status_code == 200, r.text
        db_session.refresh(first)
        db_session.refresh(second)
        assert first.status != "CANCELLED"  # a payment was recorded on it
        assert second.status == "CANCELLED"

    def test_both_sides_are_told_how_it_will_work(self, client, db_session: Session):
        _sr, tenant_user, co_tenant, _host, _occ = _approved(client, db_session, "pay8")
        co_msgs = list(db_session.scalars(select(Notification.message).where(Notification.recipient_user_id == co_tenant.id)))
        assert any("300.00 share" in m and "Rent & Deposit Payments" in m for m in co_msgs)
        renter_titles = list(db_session.scalars(select(Notification.title).where(Notification.recipient_user_id == tenant_user.id)))
        assert "You'll receive your co-tenant's rent share" in renter_titles


class TestHostThread:
    URL = "/api/users/rentals/sublet-requests/{id}/messages?thread=host"
    HOST_URL = "/api/users/hosting/sublet-requests/{id}/messages"
    PEER_URL = "/api/users/rentals/sublet-requests/{id}/messages"

    def test_host_and_co_tenant_can_talk_and_each_is_told(self, client, db_session: Session):
        sr, _tenant, co_tenant, host, _occ = _approved(client, db_session, "msg1")
        co, hc = auth_user_cookie(co_tenant), auth_user_cookie(host)
        assert client.post(self.URL.format(id=sr.id), json={"body": "Hello host"}, cookies=co).status_code == 201
        assert [m["body"] for m in client.get(self.HOST_URL.format(id=sr.id), cookies=hc).json()] == ["Hello host"]
        assert client.post(self.HOST_URL.format(id=sr.id), json={"body": "Welcome"}, cookies=hc).status_code == 201
        thread = client.get(self.URL.format(id=sr.id), cookies=co).json()
        assert [(m["body"], m["mine"]) for m in thread] == [("Hello host", True), ("Welcome", False)]
        assert db_session.query(Notification).filter_by(recipient_user_id=host.id, notification_type="sublet_message.host_received").count() == 1
        assert db_session.query(Notification).filter_by(recipient_user_id=co_tenant.id, notification_type="sublet_message.received_subtenant").count() == 1

    def test_the_renter_can_never_read_the_hosts_thread_and_it_never_mixes_with_theirs(self, client, db_session: Session):
        sr, tenant_user, co_tenant, host, _occ = _approved(client, db_session, "msg2")
        co, rc = auth_user_cookie(co_tenant), auth_user_cookie(tenant_user)
        client.post(self.URL.format(id=sr.id), json={"body": "for the host only"}, cookies=co)
        client.post(self.PEER_URL.format(id=sr.id), json={"body": "for the renter only"}, cookies=co)

        assert client.get(self.URL.format(id=sr.id), cookies=rc).status_code == 403
        assert [m["body"] for m in client.get(self.PEER_URL.format(id=sr.id), cookies=rc).json()] == ["for the renter only"]
        host_view = client.get(self.HOST_URL.format(id=sr.id), cookies=auth_user_cookie(host)).json()
        assert [m["body"] for m in host_view] == ["for the host only"]
        assert {m.thread for m in db_session.query(SubletMessage).filter_by(sublet_request_id=sr.id)} == {"HOST", "PEER"}

    def test_strangers_and_the_wrong_side_are_refused(self, client, db_session: Session):
        sr, _tenant, co_tenant, _host, _occ = _approved(client, db_session, "msg3")
        stranger = auth_user_cookie(_make_user(db_session, email="msg3-stranger@test.com"))
        assert client.get(self.URL.format(id=sr.id), cookies=stranger).status_code == 403
        assert client.get(self.HOST_URL.format(id=sr.id), cookies=stranger).status_code == 403
        # The co-tenant is not the host, so the host-side endpoint refuses them.
        assert client.get(self.HOST_URL.format(id=sr.id), cookies=auth_user_cookie(co_tenant)).status_code == 403

    def test_the_host_and_the_named_person_can_talk_as_soon_as_the_host_has_the_request(self, client, db_session: Session):
        sr, _tenant, co_tenant, host, _occ = _shared_room(db_session, "msg4")  # with the host, not yet approved
        assert sr.status == "pending_admin_review"
        assert client.get(self.URL.format(id=sr.id), cookies=auth_user_cookie(co_tenant)).status_code == 200
        assert client.get(self.HOST_URL.format(id=sr.id), cookies=auth_user_cookie(host)).status_code == 200

    def test_the_request_tells_the_screens_a_thread_exists(self, client, db_session: Session):
        sr, _tenant, co_tenant, _host, _occ = _approved(client, db_session, "msg5")
        mine = client.get("/api/users/rentals/co-tenancies", cookies=auth_user_cookie(co_tenant)).json()
        assert [r["hostThreadAvailable"] for r in mine if r["id"] == sr.id] == [True]
