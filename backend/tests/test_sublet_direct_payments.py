"""ZR-SUBLET-PAY-003 with direct payments (bank transfer / UPI / cash, no
card rail): payment arrangement, payee chain + Action Required, deposit
routing by country pack, the payment gate, report checks, how-to-pay (bank
details or UPI ID), receipts, the role-minimized transaction timeline,
landlord visibility and amendments."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.field_encryption import encrypt_json
from app.crud import rental_payment as rp_crud
from app.crud.guest import get_guest_for_user
from app.models.audit import AuditEvent
from app.models.notification import Notification
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.rental_payment import RentalPaymentInstruction
from app.models.sublet_request import SubletRequest
from app.models.user_account import UserAccount
from app.services import sublet_payments as svc
from tests.conftest import auth_user_cookie
from tests.test_sublet_arrangement_classification import _make_active_tenancy
from tests.test_sublet_payments import _approve, _obligations


def _sublet(client, db: Session, suffix: str, *, arrangement="SUBLEASE_PARTIAL", country="IN"):
    tenant_user, proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db, suffix=suffix)
    prop = db.get(Occupancy, occupancy_id).room.property
    prop.country_code, prop.jurisdiction_code = country, ("IN" if country == "IN" else "England")
    db.commit()
    body = _approve(client, db, occupancy_id, tenant_user, proposed_party_id, arrangement, suffix,
                    proposed_monthly_rent=300)
    sublet = db.get(SubletRequest, body["id"])
    host_user = db.scalar(select(UserAccount).where(UserAccount.party_id == prop.owner_party_id))
    return sublet, tenant_user, proposed_user, host_user, _obligations(db, body["newAgreementId"])


def _instruction(db: Session, party_id: int, method="UPI", details=None):
    db.add(RentalPaymentInstruction(
        party_id=party_id, status="ACTIVE", method=method, recipient_name="Asha Rao", country_code="IN",
        account_identifier_last4="bank", encrypted_bank_details=encrypt_json(details or {"upi_id": "asha@okhdfcbank"}),
        authorized_recipient_confirmed=True,
    ))
    db.commit()


class TestArrangement:
    def test_approval_creates_an_arrangement_with_payee_and_deposit_route(self, client, db_session):
        sublet, tenant_user, _p, host_user, obligations = _sublet(client, db_session, "dp1")
        a = svc.current_arrangement(db_session, sublet.id)
        assert a.rent_payee_type == "SUBLESSOR" and a.rent_payee_party_id == tenant_user.party_id
        assert a.rent_payee_basis == "SUBLET_APPROVAL_PERMITS_COLLECTION"
        assert a.deposit_route == "PAY_TO_LANDLORD_OR_AGENT"
        assert obligations["RENT"].arrangement_id == a.id and obligations["RENT"].period_end is not None

        r = client.get(f"/api/users/sublets/{sublet.id}/payment-arrangement", cookies=auth_user_cookie(tenant_user))
        body = r.json()
        assert r.status_code == 200 and body["role"] == "SUBLESSOR"
        assert body["arrangement"]["terms"]["platformFee"] == 0.0
        assert body["arrangement"]["rentPayee"]["why"]

    def test_gb_deposit_goes_to_a_protection_scheme_and_blocks_until_details_are_added(self, client, db_session):
        sublet, tenant_user, proposed_user, host_user, obligations = _sublet(client, db_session, "dp2", country="GB")
        a = svc.current_arrangement(db_session, sublet.id)
        assert a.deposit_route == "PAY_TO_CUSTODIAN"
        assert svc.evaluate(db_session, a)["deposit"]["state"] == "ACTION_REQUIRED"
        guest = get_guest_for_user(db_session, proposed_user)
        with pytest.raises(HTTPException) as exc:
            rp_crud.mark_paid(db_session, guest, obligations["DEPOSIT"], amount=10, currency=obligations["DEPOSIT"].currency,
                              declared_date=date.today(), payment_method_category="BANK_TRANSFER")
        assert "deposit protection scheme" in exc.value.detail
        r = client.post(f"/api/users/sublets/{sublet.id}/payment-arrangement", cookies=auth_user_cookie(host_user),
                        json={"custodianName": "DPS", "custodianInstructions": "Pay to DPS account 1234, ref ZR"})
        assert r.status_code == 200, r.text
        assert r.json()["arrangement"]["readiness"]["deposit"]["state"] == "READY"

    def test_gb_deposit_cannot_be_routed_to_the_sublessor(self, client, db_session):
        sublet, tenant_user, *_ = _sublet(client, db_session, "dp3", country="GB")
        r = client.post(f"/api/users/sublets/{sublet.id}/payment-arrangement", cookies=auth_user_cookie(tenant_user),
                        json={"depositRoute": "PAY_TO_SUBLESSOR"})
        assert r.status_code == 400

    def test_co_tenant_rent_cannot_be_redirected_to_the_original_tenant(self, client, db_session):
        sublet, tenant_user, *_ = _sublet(client, db_session, "dp4", arrangement="ADD_CO_TENANT")
        r = client.post(f"/api/users/sublets/{sublet.id}/payment-arrangement", cookies=auth_user_cookie(tenant_user),
                        json={"rentPayeeType": "SUBLESSOR"})
        assert r.status_code == 400
        assert "isn't authorized" in r.json()["detail"]

    def test_subtenant_cannot_change_the_arrangement(self, client, db_session):
        sublet, _t, proposed_user, *_ = _sublet(client, db_session, "dp5")
        r = client.post(f"/api/users/sublets/{sublet.id}/payment-arrangement", cookies=auth_user_cookie(proposed_user),
                        json={"acceptedMethods": ["CASH"]})
        assert r.status_code == 403

    def test_switching_rent_to_the_landlord_reroutes_only_unpaid_obligations(self, client, db_session):
        sublet, tenant_user, _p, host_user, obligations = _sublet(client, db_session, "dp6")
        r = client.post(f"/api/users/sublets/{sublet.id}/payment-arrangement", cookies=auth_user_cookie(tenant_user),
                        json={"rentPayeeType": "LANDLORD_AGENT"})
        assert r.status_code == 200, r.text
        db_session.refresh(obligations["RENT"])
        assert obligations["RENT"].recipient_party_id == host_user.party_id
        assert obligations["RENT"].payee_type == "LANDLORD_AGENT"

    def test_stale_version_conflicts(self, client, db_session):
        sublet, tenant_user, *_ = _sublet(client, db_session, "dp7")
        r = client.post(f"/api/users/sublets/{sublet.id}/payment-arrangement", cookies=auth_user_cookie(tenant_user),
                        headers={"If-Match": "99"}, json={"acceptedMethods": ["CASH"]})
        assert r.status_code == 409

    def test_after_lock_a_change_is_a_versioned_amendment(self, client, db_session):
        sublet, tenant_user, _p, host_user, obligations = _sublet(client, db_session, "dp8")
        a = svc.current_arrangement(db_session, sublet.id)
        rp_crud.record_receipt_as_recipient(db_session, db_session.get(Party, tenant_user.party_id), obligations["RENT"],
                                            amount=None, received_date=date.today(), payment_method_category="UPI")
        assert svc.is_locked(db_session, a)
        r = client.post(f"/api/users/sublets/{sublet.id}/payment-arrangement", cookies=auth_user_cookie(tenant_user),
                        json={"acceptedMethods": ["CASH"]})
        assert r.status_code == 409
        r = client.post(f"/api/users/sublets/{sublet.id}/payment-arrangement", cookies=auth_user_cookie(tenant_user),
                        json={"acceptedMethods": ["CASH"], "amendmentReason": "Landlord agreed cash"})
        assert r.status_code == 200, r.text
        assert r.json()["arrangement"]["versionNo"] == 2
        db_session.expire_all()
        assert db_session.get(type(a), a.id).status == "SUPERSEDED"


class TestPaymentGate:
    def test_payment_setup_incomplete_blocks_until_the_payee_adds_details(self, client, db_session):
        sublet, tenant_user, proposed_user, _h, obligations = _sublet(client, db_session, "dg1")
        client.post(f"/api/users/sublets/{sublet.id}/payment-arrangement", cookies=auth_user_cookie(tenant_user),
                    json={"acceptedMethods": ["BANK_TRANSFER", "UPI"]})
        guest = get_guest_for_user(db_session, proposed_user)
        rent = obligations["RENT"]
        db_session.refresh(rent)
        with pytest.raises(HTTPException) as exc:
            rp_crud.mark_paid(db_session, guest, rent, amount=300, currency=rent.currency, declared_date=date.today(),
                              payment_method_category="UPI")
        assert "Payment setup incomplete" in exc.value.detail
        assert db_session.scalar(select(Notification).where(
            Notification.notification_type == "sublet.payee_setup_incomplete")) is not None
        _instruction(db_session, tenant_user.party_id)
        record = rp_crud.mark_paid(db_session, guest, rent, amount=300, currency=rent.currency,
                                   declared_date=date.today(), payment_method_category="UPI", external_reference="UTR123")
        assert record.status == "PAYER_RECORDED"

    def test_a_cancelled_sublet_blocks_payments_to_the_original_tenant(self, client, db_session):
        sublet, _t, proposed_user, _h, obligations = _sublet(client, db_session, "dg2")
        sublet.status = "cancelled_by_authority"
        db_session.commit()
        guest = get_guest_for_user(db_session, proposed_user)
        with pytest.raises(HTTPException) as exc:
            rp_crud.mark_paid(db_session, guest, obligations["RENT"], amount=300, currency=obligations["RENT"].currency,
                              declared_date=date.today(), payment_method_category="CASH")
        assert exc.value.status_code == 409

    def test_report_must_match_currency_and_not_exceed_what_is_owed(self, client, db_session):
        _s, _t, proposed_user, _h, obligations = _sublet(client, db_session, "dg3")
        guest = get_guest_for_user(db_session, proposed_user)
        rent = obligations["RENT"]
        with pytest.raises(HTTPException) as exc:
            rp_crud.mark_paid(db_session, guest, rent, amount=300, currency="USD" if rent.currency != "USD" else "EUR",
                              declared_date=date.today(), payment_method_category="CASH")
        assert exc.value.status_code == 400
        with pytest.raises(HTTPException) as exc:
            rp_crud.mark_paid(db_session, guest, rent, amount=301, currency=rent.currency, declared_date=date.today(),
                              payment_method_category="CASH")
        assert "more than" in exc.value.detail
        rp_crud.mark_paid(db_session, guest, rent, amount=300, currency=rent.currency, declared_date=date.today(),
                          payment_method_category="CASH")
        with pytest.raises(HTTPException):  # already awaiting confirmation for the full amount
            rp_crud.mark_paid(db_session, guest, rent, amount=1, currency=rent.currency, declared_date=date.today(),
                              payment_method_category="CASH")


class TestHowToPayReceiptsAndTimeline:
    def test_how_to_pay_shows_the_upi_id_and_audits_the_view(self, client, db_session):
        sublet, tenant_user, proposed_user, _h, obligations = _sublet(client, db_session, "dh1")
        _instruction(db_session, tenant_user.party_id)
        rent = obligations["RENT"]
        r = client.get(f"/api/users/rental-payments/obligations/{rent.id}/how-to-pay", cookies=auth_user_cookie(proposed_user))
        body = r.json()
        assert r.status_code == 200, r.text
        assert body["platformFee"] == 0.0 and body["payee"]["type"] == "SUBLESSOR" and body["payee"]["why"]
        assert body["method"] == "UPI" and body["details"] == {"upi_id": "asha@okhdfcbank"}
        assert body["paymentReference"] == rent.payment_reference
        assert "does not receive" in body["zoikoNotice"]
        assert db_session.scalar(select(AuditEvent).where(AuditEvent.action == "rental_payment.instructions_viewed"))

    def test_only_the_payer_sees_how_to_pay(self, client, db_session):
        _s, tenant_user, _p, host_user, obligations = _sublet(client, db_session, "dh2")
        r = client.get(f"/api/users/rental-payments/obligations/{obligations['RENT'].id}/how-to-pay",
                       cookies=auth_user_cookie(host_user))
        assert r.status_code == 403

    def test_receipt_only_after_the_payee_confirms(self, client, db_session):
        sublet, tenant_user, proposed_user, host_user, obligations = _sublet(client, db_session, "dh3")
        guest = get_guest_for_user(db_session, proposed_user)
        rent = obligations["RENT"]
        record = rp_crud.mark_paid(db_session, guest, rent, amount=300, currency=rent.currency,
                                   declared_date=date.today(), payment_method_category="CASH")
        r = client.get(f"/api/users/rental-payments/records/{record.id}/receipt", cookies=auth_user_cookie(proposed_user))
        assert r.status_code == 409
        rp_crud.confirm_receipt(db_session, db_session.get(Party, tenant_user.party_id), record)
        r = client.get(f"/api/users/rental-payments/records/{record.id}/receipt", cookies=auth_user_cookie(proposed_user))
        assert r.status_code == 200 and r.content.startswith(b"%PDF")
        # The landlord isn't the payee here, so they're told it was confirmed.
        assert db_session.scalar(select(Notification).where(
            Notification.notification_type == "sublet.payment_confirmed",
            Notification.recipient_user_id == host_user.id)) is not None

    def test_transactions_timeline_is_role_minimized(self, client, db_session):
        sublet, tenant_user, proposed_user, host_user, obligations = _sublet(client, db_session, "dh4")
        guest = get_guest_for_user(db_session, proposed_user)
        rent = obligations["RENT"]
        rp_crud.mark_paid(db_session, guest, rent, amount=300, currency=rent.currency, declared_date=date.today(),
                          payment_method_category="UPI", external_reference="UTR-9")
        sub = client.get(f"/api/users/sublets/{sublet.id}/transactions", cookies=auth_user_cookie(proposed_user)).json()
        landlord = client.get(f"/api/users/sublets/{sublet.id}/transactions", cookies=auth_user_cookie(host_user)).json()
        payment = next(i for i in sub["items"] if i["kind"] == "PAYMENT")
        assert payment["externalReference"] == "UTR-9" and "not independently verified" in payment["evidence"]
        landlord_payment = next(i for i in landlord["items"] if i["kind"] == "PAYMENT")
        assert "externalReference" not in landlord_payment  # landlord isn't the rent payee
        assert landlord["role"] == "LANDLORD"

    def test_strangers_cannot_see_the_sublet(self, client, db_session):
        from tests.conftest import _make_user

        sublet, *_ = _sublet(client, db_session, "dh5")
        stranger = _make_user(db_session, email="stranger-dh5@test.com")
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        stranger.party_id = party.id
        db_session.commit()
        r = client.get(f"/api/users/sublets/{sublet.id}/transactions", cookies=auth_user_cookie(stranger))
        assert r.status_code == 403
