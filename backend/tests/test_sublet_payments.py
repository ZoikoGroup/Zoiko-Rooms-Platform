"""How rent flows when a renter sublets:
- a subtenant or lodger (SUBLEASE_PARTIAL / LODGER_OR_LICENSEE) contracts
  with the original renter, so -- under the market's ORIGINAL_RENTER_PAYEE
  payee model -- pays the original renter directly, who keeps paying the
  host their own rent;
- a co-tenant (ADD_CO_TENANT) is the host's own joint tenant and pays the host;
- on a full assignment, the unpaid rent moves to the assignee.
The sublet's own negotiated rent and start date are what's owed, and the
booking only confirms (and the subtenant's move-in record appears) once the
deposit and first rent are recorded as received."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import rental_payment as rp_crud
from app.crud import sublet as sublet_crud
from app.models.leasing import Agreement
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.rental_payment import RentalPaymentObligation
from app.models.user_account import UserAccount
from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_sublet_arrangement_classification import _make_active_tenancy


def _approve(client, db: Session, occupancy_id: int, tenant_user, proposed_party_id: int, arrangement: str, suffix: str, **kw):
    occupancy = db.get(Occupancy, occupancy_id)
    occupancy.room.max_occupants = 2
    db.commit()
    request = sublet_crud.submit_sublet_request(db, tenant_user, occupancy_id, proposed_party_id, arrangement, **kw)
    admin = _make_admin(db, email=f"sp-admin-{suffix}@test.com", role="super_admin")
    r = client.post(
        f"/api/occupancy/sublet-requests/{request.id}/approve", json={"stepUpPassword": "password123"},
        cookies=auth_admin_cookie(admin),
    )
    assert r.status_code == 200, r.text
    return r.json()


def _obligations(db: Session, agreement_id: int) -> dict[str, RentalPaymentObligation]:
    return {o.obligation_type: o for o in db.scalars(
        select(RentalPaymentObligation).where(RentalPaymentObligation.agreement_id == agreement_id)
    )}


class TestSubleasePaysTheOriginalRenter:
    def test_the_subtenant_owes_the_original_renter_the_negotiated_rent_from_today(self, client, db_session: Session):
        tenant_user, _proposed, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="sp1")
        body = _approve(
            client, db_session, occupancy_id, tenant_user, proposed_party_id, "SUBLEASE_PARTIAL", "sp1",
            proposed_monthly_rent=300,
        )
        obligations = _obligations(db_session, body["newAgreementId"])
        assert obligations["RENT"].recipient_party_id == tenant_user.party_id  # the original renter, not the host
        assert float(obligations["RENT"].amount) == 300.0  # the negotiated rent
        assert obligations["RENT"].due_date == date.today()  # the sublet's own start, not the old tenancy's
        # ZR-SUBLET-PAY-003 Section 9: the deposit is routed independently by
        # the country pack -- never assumed to follow the rent to the sublessor.
        host_party_id = db_session.get(Occupancy, occupancy_id).room.property.owner_party_id
        assert obligations["DEPOSIT"].recipient_party_id == host_party_id
        assert obligations["RENT"].payee_type == "SUBLESSOR"
        assert obligations["RENT"].payment_reference.startswith("ZR-SUB")
        assert float(obligations["RENT"].platform_fee_amount) == 0

    def test_the_original_renter_can_mark_it_received_and_that_confirms_the_sublet(self, client, db_session: Session):
        tenant_user, _proposed, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="sp2")
        body = _approve(client, db_session, occupancy_id, tenant_user, proposed_party_id, "SUBLEASE_PARTIAL", "sp2")
        agreement = db_session.get(Agreement, body["newAgreementId"])
        assert agreement.status == "PAYMENT_IN_PROGRESS"

        for obligation in _obligations(db_session, agreement.id).values():
            payee = db_session.get(Party, obligation.recipient_party_id)
            assert rp_crud.recipient_holds_payment_receipt_authority(db_session, obligation, payee.id)
            rp_crud.record_receipt_as_recipient(
                db_session, payee, obligation, amount=None, received_date=date.today(),
                payment_method_category="UPI",
            )
        db_session.refresh(agreement)
        assert agreement.status == "SIGNED"
        # The subtenant now has their own move-in record -- the step the old
        # auto-signed path skipped.
        assert db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id)) is not None

    def test_the_host_cannot_collect_a_subtenants_rent(self, client, db_session: Session, monkeypatch):
        from app.core.config import settings

        # The legacy suite turns the receipt-authority rule off; this test is about that rule.
        monkeypatch.setattr(settings, "payment_receipt_authority_required", True)
        tenant_user, _proposed, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="sp3")
        body = _approve(client, db_session, occupancy_id, tenant_user, proposed_party_id, "LODGER_OR_LICENSEE", "sp3")
        rent = _obligations(db_session, body["newAgreementId"])["RENT"]
        host_party_id = db_session.get(Occupancy, occupancy_id).room.property.owner_party_id
        assert not rp_crud.recipient_holds_payment_receipt_authority(db_session, rent, host_party_id)
        with pytest.raises(HTTPException) as exc:
            rp_crud.record_receipt_as_recipient(
                db_session, db_session.get(Party, host_party_id), rent, amount=None, received_date=date.today(),
                payment_method_category="CASH",
            )
        assert exc.value.status_code == 403

    def test_the_next_month_keeps_going_to_the_original_renter(self, client, db_session: Session):
        tenant_user, _proposed, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="sp4")
        body = _approve(client, db_session, occupancy_id, tenant_user, proposed_party_id, "SUBLEASE_PARTIAL", "sp4")
        agreement = db_session.get(Agreement, body["newAgreementId"])
        original_renter = db_session.get(Party, tenant_user.party_id)
        for obligation in _obligations(db_session, agreement.id).values():
            rp_crud.record_receipt_as_recipient(
                db_session, db_session.get(Party, obligation.recipient_party_id), obligation, amount=None,
                received_date=date.today(), payment_method_category="UPI",
            )
        sub_occupancy = db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))
        assert rp_crud.resolve_rent_recipient_for_occupancy(db_session, sub_occupancy) == original_renter.id


class TestCoTenantPaysTheHost:
    def test_a_co_tenant_owes_the_host(self, client, db_session: Session):
        tenant_user, _proposed, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="sp5")
        body = _approve(client, db_session, occupancy_id, tenant_user, proposed_party_id, "ADD_CO_TENANT", "sp5")
        rent = _obligations(db_session, body["newAgreementId"])["RENT"]
        assert rent.recipient_party_id != tenant_user.party_id


class TestAssignmentMovesOpenRent:
    def test_unpaid_rent_moves_to_the_assignee(self, client, db_session: Session):
        tenant_user, proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="sp6")
        occupancy = db_session.get(Occupancy, occupancy_id)
        original_guest_id = occupancy.guest_id
        room = occupancy.room
        open_rent = rp_crud.create_obligation(
            db_session, obligation_type="RENT", tenant_guest_id=original_guest_id,
            recipient_party_id=room.property.owner_party_id, amount=500, currency="INR", due_date=date.today(),
            occupancy_id=occupancy_id,
        )
        db_session.commit()

        _approve(client, db_session, occupancy_id, tenant_user, proposed_party_id, "ASSIGNMENT_FULL", "sp6")
        db_session.refresh(open_rent)
        assignee_guest_id = db_session.get(Occupancy, occupancy_id).guest_id
        assert assignee_guest_id != original_guest_id
        assert open_rent.tenant_guest_id == assignee_guest_id
