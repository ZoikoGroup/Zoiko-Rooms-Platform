"""ZR-PAY-LINK-003 Section 15/Wireframe PAY-17: joint-tenancy payer
allocation. Covers: create_payer_allocations' validation rules (sum must
equal the obligation's own amount, at least two payers, no duplicates, no
unknown guest, settable only once and only before any payment activity);
each allocated co-payer can mark_paid/be recognized as an owner of the
obligation even though they are not its tenant_guest_id; the aggregate
status crud/rental_payment.py:recompute_obligation_status computes across
all payers' latest records (PARTIALLY_PAID until every payer's share is
confirmed, CONFIRMED once the total reaches the obligation's own amount);
and that an ordinary single-payer obligation's status computation is
unchanged by any of this."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import rental_payment as rp_crud
from tests.conftest import _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_rental_payment_records import _make_guest, _make_obligation, _make_party


def _make_joint_obligation(db: Session, *, amount: float = 1000):
    tenant_a = _make_guest(db, guest_id="G-JOINT-A")
    tenant_b = _make_guest(db, guest_id="G-JOINT-B")
    recipient = _make_party(db)
    obligation = rp_crud.create_obligation(
        db, obligation_type="RENT", tenant_guest_id=tenant_a.id, recipient_party_id=recipient.id,
        amount=amount, currency="GBP", due_date=date.today(),
    )
    return obligation, tenant_a, tenant_b, recipient


class TestCreatePayerAllocationsValidation:
    def test_amounts_must_sum_to_exactly_the_obligation_amount(self, db_session: Session):
        obligation, tenant_a, tenant_b, _recipient = _make_joint_obligation(db_session, amount=1000)
        with pytest.raises(HTTPException) as exc:
            rp_crud.create_payer_allocations(db_session, obligation, [(tenant_a.id, 400), (tenant_b.id, 400)])
        assert exc.value.status_code == 400

    def test_requires_at_least_two_payers(self, db_session: Session):
        obligation, tenant_a, _tenant_b, _recipient = _make_joint_obligation(db_session, amount=1000)
        with pytest.raises(HTTPException) as exc:
            rp_crud.create_payer_allocations(db_session, obligation, [(tenant_a.id, 1000)])
        assert exc.value.status_code == 400

    def test_rejects_a_duplicate_payer(self, db_session: Session):
        obligation, tenant_a, _tenant_b, _recipient = _make_joint_obligation(db_session, amount=1000)
        with pytest.raises(HTTPException) as exc:
            rp_crud.create_payer_allocations(db_session, obligation, [(tenant_a.id, 500), (tenant_a.id, 500)])
        assert exc.value.status_code == 400

    def test_rejects_an_unknown_guest(self, db_session: Session):
        obligation, tenant_a, _tenant_b, _recipient = _make_joint_obligation(db_session, amount=1000)
        with pytest.raises(HTTPException) as exc:
            rp_crud.create_payer_allocations(db_session, obligation, [(tenant_a.id, 500), ("G-DOES-NOT-EXIST", 500)])
        assert exc.value.status_code == 400

    def test_cannot_be_set_twice(self, db_session: Session):
        obligation, tenant_a, tenant_b, _recipient = _make_joint_obligation(db_session, amount=1000)
        rp_crud.create_payer_allocations(db_session, obligation, [(tenant_a.id, 500), (tenant_b.id, 500)])
        with pytest.raises(HTTPException) as exc:
            rp_crud.create_payer_allocations(db_session, obligation, [(tenant_a.id, 500), (tenant_b.id, 500)])
        assert exc.value.status_code == 409

    def test_cannot_be_set_once_payment_activity_exists(self, db_session: Session):
        obligation, tenant_a, _recipient = _make_obligation(db_session)
        rp_crud.mark_paid(
            db_session, tenant_a, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        tenant_c = _make_guest(db_session, guest_id="G-JOINT-LATE")
        with pytest.raises(HTTPException) as exc:
            rp_crud.create_payer_allocations(db_session, obligation, [(tenant_a.id, 425), (tenant_c.id, 425)])
        assert exc.value.status_code == 409


class TestOwnershipAndVisibility:
    def test_a_co_payer_who_is_not_tenant_guest_id_still_owns_the_obligation(self, db_session: Session):
        obligation, tenant_a, tenant_b, _recipient = _make_joint_obligation(db_session, amount=1000)
        rp_crud.create_payer_allocations(db_session, obligation, [(tenant_a.id, 500), (tenant_b.id, 500)])

        assert obligation.tenant_guest_id == tenant_a.id
        rp_crud.assert_tenant_owns_obligation(obligation, tenant_b.id)  # does not raise

    def test_an_unrelated_guest_still_does_not_own_it(self, db_session: Session):
        obligation, tenant_a, tenant_b, _recipient = _make_joint_obligation(db_session, amount=1000)
        rp_crud.create_payer_allocations(db_session, obligation, [(tenant_a.id, 500), (tenant_b.id, 500)])

        with pytest.raises(HTTPException) as exc:
            rp_crud.assert_tenant_owns_obligation(obligation, "G-SOMEONE-ELSE")
        assert exc.value.status_code == 403

    def test_a_co_payer_sees_the_obligation_in_their_own_obligations_list(self, db_session: Session):
        obligation, tenant_a, tenant_b, _recipient = _make_joint_obligation(db_session, amount=1000)
        rp_crud.create_payer_allocations(db_session, obligation, [(tenant_a.id, 500), (tenant_b.id, 500)])

        b_obligations = rp_crud.list_obligations_for_tenant(db_session, tenant_b.id)
        assert [o.id for o in b_obligations] == [obligation.id]

        items, total = rp_crud.list_obligations_for_tenant_page(db_session, tenant_b.id)
        assert total == 1
        assert [o.id for o in items] == [obligation.id]


class TestAggregateStatus:
    def test_one_payers_confirmed_share_is_partially_paid_until_the_other_confirms_too(self, db_session: Session):
        obligation, tenant_a, tenant_b, recipient = _make_joint_obligation(db_session, amount=1000)
        rp_crud.create_payer_allocations(db_session, obligation, [(tenant_a.id, 500), (tenant_b.id, 500)])

        record_a = rp_crud.mark_paid(
            db_session, tenant_a, obligation, amount=500, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        rp_crud.confirm_receipt(db_session, recipient, record_a)
        db_session.refresh(obligation)
        assert obligation.status == "PARTIALLY_PAID"

        record_b = rp_crud.mark_paid(
            db_session, tenant_b, obligation, amount=500, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        rp_crud.confirm_receipt(db_session, recipient, record_b)
        db_session.refresh(obligation)
        assert obligation.status == "CONFIRMED"

    def test_a_dispute_from_either_payer_surfaces_at_the_obligation_level(self, db_session: Session):
        obligation, tenant_a, tenant_b, recipient = _make_joint_obligation(db_session, amount=1000)
        rp_crud.create_payer_allocations(db_session, obligation, [(tenant_a.id, 500), (tenant_b.id, 500)])

        record_a = rp_crud.mark_paid(
            db_session, tenant_a, obligation, amount=500, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        rp_crud.confirm_receipt(db_session, recipient, record_a)
        rp_crud.mark_paid(
            db_session, tenant_b, obligation, amount=500, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        rp_crud.report_discrepancy(db_session, record=record_a, reason_code="AMOUNT_DIFFERENT", reported_by_party_id=recipient.id)
        db_session.refresh(obligation)
        assert obligation.status == "DISPUTED"

    def test_single_payer_obligation_status_computation_is_unchanged(self, db_session: Session):
        """Regression check: an ordinary (non-joint) obligation with no
        payer_allocations rows takes the same single-latest-record path as
        before this feature existed."""
        obligation, tenant, recipient = _make_obligation(db_session)
        assert obligation.payer_allocations == []
        record = rp_crud.mark_paid(
            db_session, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        db_session.refresh(obligation)
        assert obligation.status == "RECIPIENT_CONFIRMATION_PENDING"

        rp_crud.confirm_receipt(db_session, recipient, record)
        db_session.refresh(obligation)
        assert obligation.status == "CONFIRMED"


class TestPayerAllocationsRoute:
    def test_only_the_recipient_can_set_allocations(self, client, db_session: Session):
        obligation, tenant_a, tenant_b, recipient = _make_joint_obligation(db_session, amount=1000)
        outsider = _make_user(db_session, email="joint-outsider@test.com")
        r = client.post(
            f"/api/users/rental-payments/recipient/obligations/{obligation.id}/payer-allocations",
            json={"allocations": [
                {"payerGuestId": tenant_a.id, "allocatedAmount": 500},
                {"payerGuestId": tenant_b.id, "allocatedAmount": 500},
            ]},
            cookies=auth_user_cookie(outsider),
        )
        assert r.status_code == 400, r.text

    def test_recipient_can_set_allocations_via_the_route(self, client, db_session: Session):
        obligation, tenant_a, tenant_b, recipient = _make_joint_obligation(db_session, amount=1000)
        recipient_user = _make_user(db_session, email="joint-recipient@test.com")
        recipient_user.party_id = recipient.id
        db_session.commit()

        r = client.post(
            f"/api/users/rental-payments/recipient/obligations/{obligation.id}/payer-allocations",
            json={"allocations": [
                {"payerGuestId": tenant_a.id, "allocatedAmount": 500},
                {"payerGuestId": tenant_b.id, "allocatedAmount": 500},
            ]},
            cookies=auth_user_cookie(recipient_user),
        )
        assert r.status_code == 200, r.text
        assert len(r.json()["payerAllocations"]) == 2
