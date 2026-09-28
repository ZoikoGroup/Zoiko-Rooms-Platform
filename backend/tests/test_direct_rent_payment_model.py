"""Zoiko Rooms Payment Model: Zoiko collects only the host's Listing Fee
through Stripe. Rent is paid straight to the host (bank transfer, UPI or
cash) and recording it is optional -- Zoiko creates no rent checkout, needs
no Stripe Connect account for rent, and never treats a Stripe webhook as
evidence of rent. Covers:
- the card rent rail being refused while rent_card_checkout_enabled is off
  (the production default);
- the host marking rent/deposit as received directly, which is what
  confirms the booking once the deposit and first rent are in."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import rental_payment as rp_crud
from app.crud.guest import get_guest_for_user
from app.models.leasing import Agreement
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.rental_payment import RentalPaymentRecord
from app.models.user_account import UserAccount
from app.services.payment_boundary import capabilities_snapshot
from app.crud import rental_payment_provider_account as rpa_crud
from tests.conftest import _make_user, auth_user_cookie
from tests.test_rental_payment_legacy_bridge import (
    _create_signed_agreement,
    _legacy_obligations_for,
    _rental_payment_obligations_for,
)

boundary = pytest.mark.payment_boundary


def _host_user_for(db: Session, party_id: int) -> UserAccount:
    """A host login for the recipient party -- the signed-agreement fixture's
    recipient is an operator party with no user account of its own."""
    user = db.scalar(select(UserAccount).where(UserAccount.party_id == party_id))
    if user is None:
        user = _make_user(db, email=f"host-party-{party_id}@test.com")
        user.party_id = party_id
        db.commit()
    return user


@boundary
class TestCardRentRailIsOffByDefault:
    def test_capabilities_tell_the_frontend_card_rent_checkout_is_off(self):
        snapshot = capabilities_snapshot()
        assert snapshot["rent_card_checkout_enabled"] is False
        assert snapshot["rental_money_movement_via_zoiko"] is False

    def test_a_renter_cannot_open_a_rent_checkout(self, client, db_session: Session):
        user, user_cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="dm-card")
        rent = _rental_payment_obligations_for(db_session, agreement_id)["RENT"]
        r = client.post(f"/api/users/rental-payments/obligations/{rent.id}/payment-session", cookies=user_cookies)
        assert r.status_code == 403, r.text
        assert "paid directly to your host" in r.json()["detail"]

    def test_a_host_is_never_asked_to_create_a_stripe_account_for_rent(self, client, db_session: Session):
        _user, _cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="dm-connect")
        rent = _rental_payment_obligations_for(db_session, agreement_id)["RENT"]
        host_cookies = auth_user_cookie(_host_user_for(db_session, rent.recipient_party_id))
        r = client.post(
            "/api/users/rental-payments/recipient/provider-account",
            json={"country": "GB", "email": "host@test.com"}, cookies=host_cookies,
        )
        assert r.status_code == 403, r.text

    def test_the_rent_webhook_never_takes_a_stripe_event_as_rent_evidence(self, client):
        r = client.post(
            "/api/finance/rental-payments/stripe/webhook",
            content=b'{"id": "evt_forged", "type": "checkout.session.completed"}',
            headers={"stripe-signature": "t=1,v1=forged"},
        )
        assert r.status_code == 200
        assert r.json() == {"received": True, "ignored": True}

    def test_a_connected_stripe_account_does_not_make_a_room_payment_ready(self, client, db_session: Session):
        from app.crud.payment_connection import get_payment_connection_for_room

        _user, _cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix="dm-conn")
        rent = _rental_payment_obligations_for(db_session, agreement_id)["RENT"]
        recipient = db_session.get(Party, rent.recipient_party_id)
        account = rpa_crud.create_connected_account(db_session, recipient, country="GB", email="host@test.com")
        rpa_crud.simulate_onboarding_complete(db_session, account)

        connection = get_payment_connection_for_room(db_session, rent.room)
        assert connection.destination_method != "ONLINE_PROVIDER"


class TestHostMarksRentReceived:
    def _setup(self, client, db_session: Session, suffix: str):
        user, _user_cookies, agreement_id, _start = _create_signed_agreement(client, db_session, email_suffix=suffix)
        obligations = _rental_payment_obligations_for(db_session, agreement_id)
        recipient = db_session.get(Party, obligations["RENT"].recipient_party_id)
        return user, agreement_id, obligations, recipient

    def test_marking_deposit_and_first_rent_received_confirms_the_booking(self, client, db_session: Session):
        _user, agreement_id, obligations, recipient = self._setup(client, db_session, "dm-mark1")
        for obligation in (obligations["DEPOSIT"], obligations["RENT"]):
            rp_crud.record_receipt_as_recipient(
                db_session, recipient, obligation, amount=None, received_date=date.today(),
                payment_method_category="UPI", external_reference="UTR123456",
            )
            db_session.refresh(obligation)
            assert obligation.status == "CONFIRMED"

        legacy = _legacy_obligations_for(db_session, agreement_id)
        assert {o.obligation_type: o.status for o in legacy} == {"RENT": "PAID", "DEPOSIT": "PAID"}
        agreement = db_session.get(Agreement, agreement_id)
        db_session.refresh(agreement)
        assert agreement.status == "SIGNED"
        assert db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id)) is not None

        record = db_session.scalar(select(RentalPaymentRecord).where(RentalPaymentRecord.obligation_id == obligations["RENT"].id))
        assert record.provenance == "RECIPIENT_CONFIRMATION"
        assert record.confirmed_by_party_id == recipient.id
        assert record.payment_method_category == "UPI"

    def test_a_part_payment_leaves_the_rest_due(self, client, db_session: Session):
        _user, _agreement_id, obligations, recipient = self._setup(client, db_session, "dm-mark2")
        rent = obligations["RENT"]
        rp_crud.record_receipt_as_recipient(
            db_session, recipient, rent, amount=1000, received_date=date.today(), payment_method_category="CASH",
        )
        db_session.refresh(rent)
        assert rent.status == "PARTIALLY_PAID"
        assert rent.outstanding_amount == round(float(rent.amount) - 1000, 2)

        rp_crud.record_receipt_as_recipient(
            db_session, recipient, rent, amount=None, received_date=date.today(), payment_method_category="CASH",
        )
        db_session.refresh(rent)
        assert rent.status == "CONFIRMED"
        assert rent.outstanding_amount == 0.0

    def test_more_than_is_owed_is_refused(self, client, db_session: Session):
        _user, _agreement_id, obligations, recipient = self._setup(client, db_session, "dm-mark3")
        rent = obligations["RENT"]
        with pytest.raises(HTTPException) as exc:
            rp_crud.record_receipt_as_recipient(
                db_session, recipient, rent, amount=float(rent.amount) + 1, received_date=date.today(),
                payment_method_category="BANK_TRANSFER",
            )
        assert exc.value.status_code == 400

    def test_only_the_recipient_can_mark_it_received(self, client, db_session: Session):
        _user, _agreement_id, obligations, _recipient = self._setup(client, db_session, "dm-mark4")
        stranger = Party(party_type="individual")
        db_session.add(stranger)
        db_session.commit()
        with pytest.raises(HTTPException) as exc:
            rp_crud.record_receipt_as_recipient(
                db_session, stranger, obligations["RENT"], amount=None, received_date=date.today(),
                payment_method_category="BANK_TRANSFER",
            )
        assert exc.value.status_code == 403

    def test_a_payment_the_renter_already_recorded_must_be_confirmed_instead(self, client, db_session: Session):
        user, _agreement_id, obligations, recipient = self._setup(client, db_session, "dm-mark5")
        rent = obligations["RENT"]
        guest = get_guest_for_user(db_session, user)
        rp_crud.mark_paid(
            db_session, guest, rent, amount=float(rent.amount), currency=rent.currency,
            declared_date=date.today(), payment_method_category="BANK_TRANSFER",
        )
        with pytest.raises(HTTPException) as exc:
            rp_crud.record_receipt_as_recipient(
                db_session, recipient, rent, amount=None, received_date=date.today(),
                payment_method_category="BANK_TRANSFER",
            )
        assert exc.value.status_code == 409

    def test_the_host_can_mark_it_received_over_the_api(self, client, db_session: Session):
        _user, _agreement_id, obligations, recipient = self._setup(client, db_session, "dm-mark6")
        rent = obligations["RENT"]
        host_cookies = auth_user_cookie(_host_user_for(db_session, recipient.id))
        r = client.post(
            f"/api/users/rental-payments/recipient/obligations/{rent.id}/record-receipt",
            json={"receivedDate": date.today().isoformat(), "paymentMethodCategory": "UPI", "externalReference": "UTR1"},
            cookies=host_cookies,
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "CONFIRMED"
        assert r.json()["outstandingAmount"] == 0.0
