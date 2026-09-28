"""Deposit returns and cancellation returns paid host -> renter directly --
recorded by the host, confirmed (or questioned) by the renter. Zoiko never
holds or sends back this money."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import rental_payment as rp_crud
from app.crud import rental_payment_return as returns_crud
from app.crud.guest import get_guest_for_user
from app.models.leasing import Agreement
from app.models.occupancy import Occupancy
from app.models.party import Party
from tests.conftest import auth_user_cookie
from tests.test_direct_rent_payment_model import _host_user_for
from tests.test_rental_payment_legacy_bridge import _create_signed_agreement, _rental_payment_obligations_for


def _paid_booking(client, db_session: Session, suffix: str, *, occupancy_status: str):
    """A booking whose deposit and first rent the host marked received,
    moved to the given end state."""
    user, _cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix=suffix)
    obligations = _rental_payment_obligations_for(db_session, agreement_id)
    recipient = db_session.get(Party, obligations["RENT"].recipient_party_id)
    for obligation in obligations.values():
        rp_crud.record_receipt_as_recipient(
            db_session, recipient, obligation, amount=None, received_date=date.today(), payment_method_category="UPI",
        )
    agreement = db_session.get(Agreement, agreement_id)
    occupancy = db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))
    occupancy.status = occupancy_status
    db_session.commit()
    return user, recipient, occupancy, obligations


class TestDepositReturn:
    def test_host_returns_the_deposit_with_a_deduction_and_the_renter_confirms(self, client, db_session: Session):
        user, recipient, occupancy, obligations = _paid_booking(client, db_session, "rt-dep", occupancy_status="ENDED")
        deposit = float(obligations["DEPOSIT"].amount)
        record = returns_crud.record_return(
            db_session, recipient, occupancy, kind="DEPOSIT_RETURN", amount=deposit - 50, deductions_amount=50,
            deductions_reason="Broken lamp", payment_method_category="BANK_TRANSFER", returned_date=date.today(),
        )
        assert record.status == "RECORDED"
        assert returns_crud.booking_money_summary(db_session, occupancy)["deposit_settled"] == deposit

        guest = get_guest_for_user(db_session, user)
        confirmed = returns_crud.tenant_confirm_return(db_session, guest, record)
        assert confirmed.status == "CONFIRMED"

    def test_more_than_the_deposit_is_refused(self, client, db_session: Session):
        _user, recipient, occupancy, obligations = _paid_booking(client, db_session, "rt-over", occupancy_status="ENDED")
        with pytest.raises(HTTPException) as exc:
            returns_crud.record_return(
                db_session, recipient, occupancy, kind="DEPOSIT_RETURN",
                amount=float(obligations["DEPOSIT"].amount) + 1, payment_method_category="UPI", returned_date=date.today(),
            )
        assert exc.value.status_code == 400

    def test_a_deduction_needs_a_reason(self, client, db_session: Session):
        _user, recipient, occupancy, _obligations = _paid_booking(client, db_session, "rt-reason", occupancy_status="ENDED")
        with pytest.raises(HTTPException) as exc:
            returns_crud.record_return(
                db_session, recipient, occupancy, kind="DEPOSIT_RETURN", amount=10, deductions_amount=20,
                payment_method_category="CASH", returned_date=date.today(),
            )
        assert exc.value.status_code == 400

    def test_a_deposit_is_only_returned_after_the_tenancy_ends(self, client, db_session: Session):
        _user, recipient, occupancy, _obligations = _paid_booking(client, db_session, "rt-early", occupancy_status="ACTIVE")
        with pytest.raises(HTTPException) as exc:
            returns_crud.record_return(
                db_session, recipient, occupancy, kind="DEPOSIT_RETURN", amount=10, payment_method_category="UPI",
                returned_date=date.today(),
            )
        assert exc.value.status_code == 409

    def test_a_disputed_return_frees_the_amount_to_be_recorded_again(self, client, db_session: Session):
        user, recipient, occupancy, obligations = _paid_booking(client, db_session, "rt-disp", occupancy_status="ENDED")
        deposit = float(obligations["DEPOSIT"].amount)
        record = returns_crud.record_return(
            db_session, recipient, occupancy, kind="DEPOSIT_RETURN", amount=deposit, payment_method_category="UPI",
            returned_date=date.today(),
        )
        guest = get_guest_for_user(db_session, user)
        disputed = returns_crud.tenant_dispute_return(db_session, guest, record, details="Nothing arrived")
        assert disputed.status == "DISPUTED"
        # The disputed one no longer counts, so the host can record the resend.
        again = returns_crud.record_return(
            db_session, recipient, occupancy, kind="DEPOSIT_RETURN", amount=deposit, payment_method_category="UPI",
            returned_date=date.today(), external_reference="RESENT",
        )
        assert again.status == "RECORDED"

    def test_only_the_host_who_was_paid_can_record_it(self, client, db_session: Session):
        _user, _recipient, occupancy, _obligations = _paid_booking(client, db_session, "rt-who", occupancy_status="ENDED")
        stranger = Party(party_type="individual")
        db_session.add(stranger)
        db_session.commit()
        with pytest.raises(HTTPException) as exc:
            returns_crud.record_return(
                db_session, stranger, occupancy, kind="DEPOSIT_RETURN", amount=10, payment_method_category="UPI",
                returned_date=date.today(),
            )
        assert exc.value.status_code == 403


class TestCancellationReturn:
    def test_host_sends_back_what_was_paid_on_a_cancelled_booking(self, client, db_session: Session):
        _user, recipient, occupancy, obligations = _paid_booking(client, db_session, "rt-cancel", occupancy_status="CANCELLED")
        paid = sum(float(o.amount) for o in obligations.values())
        record = returns_crud.record_return(
            db_session, recipient, occupancy, kind="CANCELLATION_RETURN", amount=paid - 100, deductions_amount=100,
            deductions_reason="Cancellation fee per agreement", payment_method_category="BANK_TRANSFER",
            returned_date=date.today(),
        )
        assert float(record.amount) == round(paid - 100, 2)

    def test_the_api_round_trip_host_records_renter_confirms(self, client, db_session: Session):
        user, recipient, occupancy, obligations = _paid_booking(client, db_session, "rt-api", occupancy_status="CANCELLED")
        host_cookies = auth_user_cookie(_host_user_for(db_session, recipient.id))

        r = client.get("/api/users/rental-payments/recipient/returns/candidates", cookies=host_cookies)
        assert r.status_code == 200, r.text
        candidate = next(c for c in r.json() if c["occupancyId"] == occupancy.id)
        assert candidate["kind"] == "CANCELLATION_RETURN"
        assert candidate["remaining"] == round(sum(float(o.amount) for o in obligations.values()), 2)

        r = client.post(
            f"/api/users/rental-payments/recipient/occupancies/{occupancy.id}/returns",
            json={"kind": "CANCELLATION_RETURN", "amount": candidate["remaining"], "paymentMethodCategory": "UPI",
                  "returnedDate": date.today().isoformat(), "externalReference": "UTR99"},
            cookies=host_cookies,
        )
        assert r.status_code == 201, r.text
        return_id = r.json()["id"]

        renter_cookies = auth_user_cookie(user)
        mine = client.get("/api/users/rental-payments/returns", cookies=renter_cookies).json()
        assert any(item["id"] == return_id for item in mine)
        r = client.post(f"/api/users/rental-payments/returns/{return_id}/confirm", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CONFIRMED"
