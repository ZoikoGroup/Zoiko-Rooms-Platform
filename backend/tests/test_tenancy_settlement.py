"""Money when a tenancy ends -- crud/tenancy_settlement.py. Unpaid rent after
the end date is cancelled; rent paid to the host for days after it becomes a
refund the host owes, settled between host and renter. No admin step."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.crud.tenancy_settlement import apply_host_refund_deadlines
from app.models.host_refund import HostRefund
from app.models.occupancy import Occupancy
from app.models.rental_payment import RentalPaymentObligation
from app.models.termination_case import TerminationCase
from tests.conftest import auth_user_cookie
from tests.test_host_tenancy import _setup
from tests.test_termination_party_decisions import _host_ground, _occupancy


def _rent(db: Session, occ: Occupancy, host, *, start: date, end: date, status: str, amount: float = 300.0) -> RentalPaymentObligation:
    ob = RentalPaymentObligation(
        obligation_type="RENT", occupancy_id=occ.id, tenant_guest_id=occ.guest_id, recipient_party_id=host.party_id,
        amount=amount, currency="INR", due_date=start, status=status, period_start=start, period_end=end,
    )
    db.add(ob)
    db.commit()
    return ob


def _end_tenancy_today(client, db_session: Session, suffix: str):
    agreement_id, host, renter = _setup(client, db_session, suffix)
    occ = _occupancy(db_session, agreement_id)
    case = _host_ground(client, host, occ.id).json()
    client.post(f"/api/users/rentals/termination-cases/{case['id']}/respond", json={"response": "ACCEPT"}, cookies=auth_user_cookie(renter))
    row = db_session.get(TerminationCase, case["id"])
    row.effective_termination_date = date.today()
    db_session.commit()
    return occ, host, renter


def _hand_back(client, occ, host, renter):
    client.post(f"/api/users/rentals/occupancies/{occ.id}/move-out/notice", json={}, cookies=auth_user_cookie(renter))
    client.post(f"/api/users/rentals/occupancies/{occ.id}/move-out/ready", json={}, cookies=auth_user_cookie(renter))
    r = client.post(f"/api/users/hosting/occupancies/{occ.id}/move-out/confirm", json={}, cookies=auth_user_cookie(host))
    assert r.status_code == 200, r.text


class TestSettlementOnEnd:
    def test_future_rent_cancelled_and_prepaid_rent_refunded_pro_rata(self, client, db_session: Session):
        occ, host, renter = _end_tenancy_today(client, db_session, "sa")
        today = date.today()
        # Paid, fully after the end: refunded in full.
        future_paid = _rent(db_session, occ, host, start=today + timedelta(days=1), end=today + timedelta(days=30), status="CONFIRMED")
        # Paid, straddling the end: 10 of 20 days are after it.
        _rent(db_session, occ, host, start=today - timedelta(days=9), end=today + timedelta(days=10), status="CONFIRMED", amount=200.0)
        # Not paid, after the end: not owed.
        unpaid = _rent(db_session, occ, host, start=today + timedelta(days=31), end=today + timedelta(days=60), status="DUE")

        _hand_back(client, occ, host, renter)

        db_session.expire_all()
        assert db_session.get(Occupancy, occ.id).status == "ENDED"
        assert db_session.get(RentalPaymentObligation, unpaid.id).status == "CANCELLED"
        assert db_session.get(RentalPaymentObligation, future_paid.id).status == "CONFIRMED"
        [refund] = db_session.query(HostRefund).filter_by(occupancy_id=occ.id).all()
        assert float(refund.amount) == 400.0  # 300 + 200 * 10/20
        assert refund.status == "DUE"

        listed = client.get("/api/users/rentals/refunds", cookies=auth_user_cookie(renter)).json()
        assert [x["id"] for x in listed] == [refund.id]

    def test_host_marks_paid_and_renter_confirms(self, client, db_session: Session):
        occ, host, renter = _end_tenancy_today(client, db_session, "sb")
        _rent(db_session, occ, host, start=date.today() + timedelta(days=1), end=date.today() + timedelta(days=30), status="CONFIRMED")
        _hand_back(client, occ, host, renter)
        refund_id = client.get("/api/users/hosting/refunds", cookies=auth_user_cookie(host)).json()[0]["id"]

        assert client.post(f"/api/users/rentals/refunds/{refund_id}/confirm", cookies=auth_user_cookie(renter)).status_code == 409
        r = client.post(f"/api/users/hosting/refunds/{refund_id}/mark-paid", json={"reference": "UPI 1234"}, cookies=auth_user_cookie(host))
        assert r.status_code == 200 and r.json()["status"] == "MARKED_PAID"
        r = client.post(f"/api/users/rentals/refunds/{refund_id}/confirm", cookies=auth_user_cookie(renter))
        assert r.json()["status"] == "CONFIRMED" and r.json()["deemedConfirmed"] is False

    def test_not_received_then_paid_again(self, client, db_session: Session):
        occ, host, renter = _end_tenancy_today(client, db_session, "sc")
        _rent(db_session, occ, host, start=date.today() + timedelta(days=1), end=date.today() + timedelta(days=30), status="CONFIRMED")
        _hand_back(client, occ, host, renter)
        refund_id = client.get("/api/users/hosting/refunds", cookies=auth_user_cookie(host)).json()[0]["id"]
        client.post(f"/api/users/hosting/refunds/{refund_id}/mark-paid", json={}, cookies=auth_user_cookie(host))
        r = client.post(f"/api/users/rentals/refunds/{refund_id}/not-received", json={"note": "Nothing arrived"}, cookies=auth_user_cookie(renter))
        assert r.json()["status"] == "NOT_RECEIVED"
        r = client.post(f"/api/users/hosting/refunds/{refund_id}/mark-paid", json={"reference": "resent"}, cookies=auth_user_cookie(host))
        assert r.json()["status"] == "MARKED_PAID"

    def test_deadlines_deem_confirmation_and_flag_overdue(self, client, db_session: Session):
        occ, host, renter = _end_tenancy_today(client, db_session, "sd")
        _rent(db_session, occ, host, start=date.today() + timedelta(days=1), end=date.today() + timedelta(days=30), status="CONFIRMED")
        _hand_back(client, occ, host, renter)
        refund = db_session.query(HostRefund).filter_by(occupancy_id=occ.id).one()

        refund.due_by = date.today() - timedelta(days=1)
        db_session.commit()
        assert apply_host_refund_deadlines(db_session) == 1
        db_session.refresh(refund)
        assert refund.status == "OVERDUE"

        refund.status = "MARKED_PAID"
        refund.marked_paid_at = datetime.now(timezone.utc) - timedelta(days=8)
        db_session.commit()
        apply_host_refund_deadlines(db_session)
        db_session.refresh(refund)
        assert refund.status == "CONFIRMED" and refund.deemed_confirmed is True

    def test_strangers_cannot_touch_the_refund(self, client, db_session: Session):
        from tests.conftest import _make_user

        occ, host, renter = _end_tenancy_today(client, db_session, "se")
        _rent(db_session, occ, host, start=date.today() + timedelta(days=1), end=date.today() + timedelta(days=30), status="CONFIRMED")
        _hand_back(client, occ, host, renter)
        refund_id = db_session.query(HostRefund).filter_by(occupancy_id=occ.id).one().id
        stranger = _make_user(db_session, email="settle-stranger@test.com")
        db_session.commit()
        assert client.post(f"/api/users/hosting/refunds/{refund_id}/mark-paid", json={}, cookies=auth_user_cookie(stranger)).status_code == 404
        assert client.post(f"/api/users/rentals/refunds/{refund_id}/confirm", cookies=auth_user_cookie(stranger)).status_code == 404
