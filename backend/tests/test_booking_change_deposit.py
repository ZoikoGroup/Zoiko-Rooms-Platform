"""ZR-ENG-CLR-008 Section 4/11/DEPOSIT_CHANGE: 'Deposit amount/instrument/
custody terms -- Section 2 authoritative; never auto-mutated.' Approving
this BCR type is deliberately request/decide only -- it never writes to
DepositRecord.held_amount itself; Section 2's own release/claim endpoints
(crud/finance.py) remain the only way deposit money actually moves."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.booking_change_request import BookingChangeRequest
from app.models.finance import DepositRecord, Obligation
from tests.conftest import auth_user_cookie
from tests.test_booking_change_requests import _signed_agreement_before_move_in


def _current_deposit_amount(db_session: Session, agreement_id: int) -> float:
    obligation = db_session.query(Obligation).filter(
        Obligation.agreement_id == agreement_id, Obligation.obligation_type == "DEPOSIT",
    ).first()
    record = db_session.query(DepositRecord).filter(DepositRecord.obligation_id == obligation.id).first()
    return float(record.held_amount)


class TestRequestDepositChange:
    def test_renter_can_request_a_deposit_change(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="dep-01", start_date=date.today() - timedelta(days=5),
        )
        current = _current_deposit_amount(db_session, agreement_id)

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/deposit-change-requests",
            json={"proposedDepositAmount": current + 100, "reason": "Agreed to a higher deposit"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["changeType"] == "DEPOSIT_CHANGE"
        assert body["status"] == "AWAITING_HOST"
        assert body["originalDepositAmount"] == current
        assert body["proposedDepositAmount"] == current + 100

    def test_cannot_propose_the_same_amount(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="dep-02", start_date=date.today() - timedelta(days=5),
        )
        current = _current_deposit_amount(db_session, agreement_id)

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/deposit-change-requests",
            json={"proposedDepositAmount": current},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 400, r.text

    def test_negative_amount_is_rejected(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="dep-03", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/deposit-change-requests",
            json={"proposedDepositAmount": -50},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 422, r.text


class TestApproveDepositChange:
    def test_approval_reaches_effective_without_touching_the_deposit_record(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="dep-04", start_date=date.today() - timedelta(days=5),
        )
        current = _current_deposit_amount(db_session, agreement_id)

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/deposit-change-requests",
            json={"proposedDepositAmount": current + 200},
            cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "EFFECTIVE"

        # The whole point: approval never itself changes DepositRecord.held_amount.
        after = _current_deposit_amount(db_session, agreement_id)
        assert after == current

        bcr = db_session.get(BookingChangeRequest, bcr_id)
        db_session.refresh(bcr)
        assert bcr.resulting_amendment_id is None

    def test_propose_alternative_is_not_supported_for_deposit_change(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="dep-05", start_date=date.today() - timedelta(days=5),
        )
        current = _current_deposit_amount(db_session, agreement_id)
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/deposit-change-requests",
            json={"proposedDepositAmount": current + 100},
            cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        r = client.post(
            f"/api/leasing/booking-change-requests/{bcr_id}/propose-alternative",
            json={"proposedMonthlyRent": 999},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text
