"""A problem reported on a rent/deposit payment record also opens a dispute
case, so it shows on the renter's, host's and admin's Disputes pages. The
tenant and host resolve it between themselves; that decides the linked
claim and closes the case with no Zoiko Rooms review."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import disputes as disputes_crud
from app.models.guest import Guest
from app.crud import rental_payment as rp_crud
from app.models.dispute import DisputeResolutionCase
from app.models.rental_payment import RentalPaymentDispute
from app.models.user_account import UserAccount
from app.services.dispute_forum_resolver import resolve_claim_authority
from app.services.payment_dispute_cases import link_open_payment_disputes, open_linked_case
from tests.conftest import auth_admin_cookie, auth_user_cookie
from tests.test_rental_payment_dispute_resolution import _disputed_payment


def _case_for(db: Session, dispute: RentalPaymentDispute) -> DisputeResolutionCase:
    db.expire_all()
    dispute = db.get(RentalPaymentDispute, dispute.id)
    assert dispute.dispute_case_id is not None
    return db.get(DisputeResolutionCase, dispute.dispute_case_id)


def _renter(db: Session, rent) -> UserAccount:
    return db.get(UserAccount, rent.tenant.user_account_id)


class TestReportOpensACase:
    def test_reporting_a_problem_opens_a_linked_case(self, client, db_session: Session):
        rent, record, dispute, _admin = _disputed_payment(client, db_session, "pdc-open")
        case = _case_for(db_session, dispute)
        assert len(case.claims) == 1
        claim = case.claims[0]
        assert claim.claim_family == "PAYMENT"
        assert claim.claim_code == "PAYMENT_RECORD_NOT_ARRIVED"
        assert claim.source_record_type == "RENTAL_PAYMENT_DISPUTE"
        assert claim.source_record_id == str(dispute.id)
        assert float(claim.amount) == float(record.declared_amount)
        assert claim.currency == record.declared_currency
        assert claim.authority_class == "A1"

    def test_the_renter_sees_the_case_on_their_disputes_page(self, client, db_session: Session):
        rent, _record, dispute, _admin = _disputed_payment(client, db_session, "pdc-renter")
        case = _case_for(db_session, dispute)
        r = client.get("/api/users/rentals/disputes", cookies=auth_user_cookie(_renter(db_session, rent)))
        assert r.status_code == 200, r.text
        assert case.id in [c["id"] for c in r.json()]

    def test_before_move_in_both_sides_still_see_it(self, client, db_session: Session):
        """No occupancy yet (signed agreement, deposit stage): the case is
        unanchored, so access comes from the payment itself."""
        rent, _record, dispute, _admin = _disputed_payment(client, db_session, "pdc-premove")
        case = _case_for(db_session, dispute)
        assert case.occupancy_id is None
        guest = db_session.get(Guest, rent.tenant_guest_id)
        assert case.id in [c.id for c in disputes_crud.list_cases_for_guest(db_session, guest)]
        assert case.id in [c.id for c in disputes_crud.list_cases_for_party(db_session, rent.recipient_party_id)]
        disputes_crud.assert_guest_can_access_case(case, guest)
        disputes_crud.assert_party_can_access_case(case, rent.recipient_party_id)

    def test_an_unrelated_renter_cannot_see_it(self, client, db_session: Session):
        _rent, _record, dispute, _admin = _disputed_payment(client, db_session, "pdc-a")
        case = _case_for(db_session, dispute)
        other_guest = Guest(id="G-NOTPARTY")
        assert case.id not in [c.id for c in disputes_crud.list_cases_for_guest(db_session, other_guest)]
        with pytest.raises(HTTPException):
            disputes_crud.assert_guest_can_access_case(case, other_guest)

    def test_the_payment_dispute_carries_the_case_id(self, client, db_session: Session):
        _rent, _record, dispute, admin = _disputed_payment(client, db_session, "pdc-id")
        r = client.get("/api/finance/rental-payments/disputes", cookies=auth_admin_cookie(admin))
        row = next(d for d in r.json() if d["id"] == dispute.id)
        assert row["disputeCaseId"] == _case_for(db_session, dispute).id

    def test_opening_is_idempotent(self, client, db_session: Session):
        _rent, _record, dispute, _admin = _disputed_payment(client, db_session, "pdc-idem")
        case = _case_for(db_session, dispute)
        dispute = db_session.get(RentalPaymentDispute, dispute.id)
        assert open_linked_case(db_session, dispute).id == case.id
        assert link_open_payment_disputes(db_session) == 0
        assert db_session.query(DisputeResolutionCase).filter_by(occupancy_id=case.occupancy_id).count() == 1


class TestForum:
    def test_payment_record_claims_resolve_to_a1(self):
        assert resolve_claim_authority("PAYMENT", "PAYMENT_RECORD_AMOUNT_DIFFERENT").authority_class == "A1"


def _host(db: Session, rent) -> UserAccount:
    from tests.conftest import _make_user

    host = db.query(UserAccount).filter(UserAccount.party_id == rent.recipient_party_id).first()
    if host is None:
        host = _make_user(db, email=f"host-{rent.recipient_party_id}@test.com")
        host.party_id = rent.recipient_party_id
        db.flush()
    return host


class TestResolvedBetweenTenantAndHost:
    """No Zoiko Rooms decision: each side concedes only its own point, and
    the case closes on its own."""

    def _claim_id(self, db: Session, dispute) -> tuple[int, int]:
        case = _case_for(db, dispute)
        return case.id, case.claims[0].id

    def _as_renter(self, client, db, rent, case_id, claim_id, action):
        return client.post(
            f"/api/users/rentals/disputes/{case_id}/claims/{claim_id}/payment-resolution",
            json={"action": action, "notes": "test"}, cookies=auth_user_cookie(_renter(db, rent)),
        )

    def test_tenant_confirms_not_paid_and_it_is_owed_again(self, client, db_session: Session):
        rent, record, dispute, _admin = _disputed_payment(client, db_session, "pdc-p-np")
        case_id, claim_id = self._claim_id(db_session, dispute)
        r = self._as_renter(client, db_session, rent, case_id, claim_id, "CONFIRM_NOT_PAID")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CLOSED"
        db_session.expire_all()
        d = db_session.get(RentalPaymentDispute, dispute.id)
        assert (d.status, d.outcome, d.resolved_by_guest_id, d.resolved_by_admin_id) == (
            "RESOLVED", "PAYMENT_NOT_RECEIVED", rent.tenant_guest_id, None,
        )
        assert d.record.status == "REVERSED"
        case = _case_for(db_session, dispute)
        assert case.claims[0].status == "UPHELD"
        assert case.closed_by_admin_id is None

    def test_host_confirms_received_and_the_payment_stands(self, client, db_session: Session):
        rent, record, dispute, _admin = _disputed_payment(client, db_session, "pdc-p-rec")
        dispute = db_session.get(RentalPaymentDispute, dispute.id)
        rp_crud.resolve_dispute_by_party(db_session, dispute, action="CONFIRM_RECEIVED", party_id=rent.recipient_party_id)
        db_session.expire_all()
        d = db_session.get(RentalPaymentDispute, dispute.id)
        assert (d.outcome, d.resolved_by_party_id) == ("PAYMENT_STANDS", rent.recipient_party_id)
        assert d.record.status == "CONFIRMED"
        assert d.record.provenance == "RECIPIENT_CONFIRMATION"
        case = _case_for(db_session, dispute)
        assert (case.claims[0].status, case.status) == ("NOT_UPHELD", "CLOSED")

    def test_host_route_confirms_received(self, client, db_session: Session):
        rent, _record, dispute, _admin = _disputed_payment(client, db_session, "pdc-p-hr")
        host = _host(db_session, rent)
        case_id, claim_id = self._claim_id(db_session, dispute)
        r = client.post(
            f"/api/users/hosting/disputes/{case_id}/claims/{claim_id}/payment-resolution",
            json={"action": "CONFIRM_RECEIVED"}, cookies=auth_user_cookie(host),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CLOSED"

    def test_reporter_withdraws(self, client, db_session: Session):
        rent, _record, dispute, _admin = _disputed_payment(client, db_session, "pdc-p-wd")
        dispute = db_session.get(RentalPaymentDispute, dispute.id)
        rp_crud.resolve_dispute_by_party(db_session, dispute, action="WITHDRAW", party_id=rent.recipient_party_id)
        db_session.expire_all()
        d = db_session.get(RentalPaymentDispute, dispute.id)
        assert (d.outcome, d.record.status) == ("CLOSE_ONLY", "DISPUTED")
        case = _case_for(db_session, dispute)
        assert (case.claims[0].status, case.status) == ("WITHDRAWN", "CLOSED")

    def test_tenant_cannot_confirm_receipt_for_the_host(self, client, db_session: Session):
        rent, _record, dispute, _admin = _disputed_payment(client, db_session, "pdc-p-x1")
        case_id, claim_id = self._claim_id(db_session, dispute)
        assert self._as_renter(client, db_session, rent, case_id, claim_id, "CONFIRM_RECEIVED").status_code == 403

    def test_tenant_cannot_withdraw_the_hosts_report(self, client, db_session: Session):
        rent, _record, dispute, _admin = _disputed_payment(client, db_session, "pdc-p-x2")
        case_id, claim_id = self._claim_id(db_session, dispute)
        assert self._as_renter(client, db_session, rent, case_id, claim_id, "WITHDRAW").status_code == 403

    def test_host_cannot_declare_the_tenant_did_not_pay(self, client, db_session: Session):
        rent, _record, dispute, _admin = _disputed_payment(client, db_session, "pdc-p-x3")
        dispute = db_session.get(RentalPaymentDispute, dispute.id)
        with pytest.raises(HTTPException) as exc:
            rp_crud.resolve_dispute_by_party(db_session, dispute, action="CONFIRM_NOT_PAID", party_id=rent.recipient_party_id)
        assert exc.value.status_code == 403

    def test_it_cannot_be_resolved_twice(self, client, db_session: Session):
        rent, _record, dispute, _admin = _disputed_payment(client, db_session, "pdc-p-twice")
        case_id, claim_id = self._claim_id(db_session, dispute)
        assert self._as_renter(client, db_session, rent, case_id, claim_id, "CONFIRM_NOT_PAID").status_code == 200
        assert self._as_renter(client, db_session, rent, case_id, claim_id, "CONFIRM_NOT_PAID").status_code == 409

    def test_payment_claims_are_not_settled_by_a_settlement_offer(self, client, db_session: Session):
        rent, _record, dispute, _admin = _disputed_payment(client, db_session, "pdc-p-set")
        case_id, claim_id = self._claim_id(db_session, dispute)
        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/settlements",
            json={"claimIds": [claim_id], "termsText": "split it", "acknowledgesNoNonwaivableWaiver": True}, cookies=auth_user_cookie(_renter(db_session, rent)),
        )
        assert r.status_code == 409, r.text
