"""ZR-ENG-CLR-006 Section 12: the Refund Entitlement Calculation Engine.
EARNED_RENT/REFUNDABLE_UNEARNED_RENT are always real; NOTICE_LIABILITY and
MITIGATION_CREDIT are real whenever the resolved market pack's
termination_liability_model calls for a nonzero amount (AC-12/13/14 -- see
TestLiabilityModels below). RENTER_FEE/TAX/OTHER_CREDIT remain always zero
(see models/refund_entitlement.py's own module docstring for exactly why)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from sqlalchemy import select

from app.crud import refund_entitlement as refund_entitlement_crud
from app.crud import termination as termination_crud
from app.models.audit import AuditEvent
from app.models.domain_event import DomainEvent
from app.models.finance import LedgerEntry, Obligation, PaymentAllocation, PaymentSchedule, PayoutBeneficiary, SimulatedPayment
from app.models.market_policy import MarketPolicyPack
from app.models.notification import Notification
from app.schemas.termination import MitigationRecordCreate
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_termination_case import _make_active_occupancy


def _pay_obligation(db: Session, guest_id: str, obligation: Obligation, amount: float, *, suffix: str) -> None:
    payment = SimulatedPayment(
        guest_id=guest_id, amount=amount, currency="INR", idempotency_key=f"refund-entitlement-{suffix}", status="SUCCEEDED",
    )
    db.add(payment)
    db.flush()
    db.add(PaymentAllocation(payment_id=payment.id, obligation_id=obligation.id, amount_allocated=amount))
    db.commit()


class TestCalculateRefundEntitlement:
    def test_splits_earned_and_unearned_rent_around_the_effective_date(self, client, db_session: Session):
        admin = _make_admin(db_session, email="refund-calc-admin@test.com", role="super_admin")
        occupancy, guest, _listing, agreement = _make_active_occupancy(db_session, admin=admin, suffix="calc1")

        # The fixture's own initial obligation has status="PAID" set as a
        # literal (a shortcut other fixtures in this codebase also use) but
        # no real PaymentAllocation -- the calculator only trusts actual
        # allocations (matching recompute_obligation_status's own "status is
        # only ever derived from allocations" invariant), so give it one.
        initial_obligation = db_session.scalar(
            select(Obligation).where(Obligation.agreement_id == agreement.id, Obligation.obligation_type == "RENT")
        )
        _pay_obligation(db_session, guest.id, initial_obligation, 1000.0, suffix="calc1-initial")

        future_due = date.today() + timedelta(days=30)
        future_obligation = Obligation(
            obligation_type="RENT", money_plane="OCCUPANCY", amount=1000.0, currency="INR",
            due_date=future_due, status="PENDING", occupancy_id=occupancy.id,
        )
        db_session.add(future_obligation)
        db_session.commit()
        _pay_obligation(db_session, guest.id, future_obligation, 1000.0, suffix="calc1")

        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=admin_cookies,
        )
        case_id = r.json()["id"]

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["version"] == 1
        assert float(body["grossRefundable"]) == 1000.0
        assert float(body["netRefund"]) == 1000.0

        line_types = {item["type"]: float(item["amount"]) for item in body["lineItems"]}
        assert line_types["EARNED_RENT"] == 1000.0  # the fixture's own initial obligation, due today
        assert line_types["REFUNDABLE_UNEARNED_RENT"] == 1000.0  # the future-dated one just paid
        for zero_type in ("NOTICE_LIABILITY", "MITIGATION_CREDIT", "RENTER_FEE", "TAX", "OTHER_CREDIT"):
            assert line_types[zero_type] == 0.0

    def test_recalculating_creates_a_new_version_not_an_overwrite(self, client, db_session: Session):
        admin = _make_admin(db_session, email="refund-recalc-admin@test.com", role="super_admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="recalc1")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=admin_cookies,
        )
        case_id = r.json()["id"]

        r1 = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        r2 = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        assert r1.json()["version"] == 1
        assert r2.json()["version"] == 2
        assert r1.json()["id"] != r2.json()["id"]

        r = client.get(f"/api/occupancy/termination-cases/{case_id}/refund-entitlements", cookies=admin_cookies)
        assert len(r.json()) == 2

    def test_calculation_endpoint_returns_the_latest_version(self, client, db_session: Session):
        """ZR-ENG-CLR-006 Section 20.1 GET /termination-cases/{id}/calculation."""
        admin = _make_admin(db_session, email="refund-calcget-admin@test.com", role="super_admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="calcget1")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=admin_cookies,
        )
        case_id = r.json()["id"]

        r = client.get(f"/api/occupancy/termination-cases/{case_id}/calculation", cookies=admin_cookies)
        assert r.status_code == 404, r.text

        r1 = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        r2 = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)

        r = client.get(f"/api/occupancy/termination-cases/{case_id}/calculation", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["id"] == r2.json()["id"]
        assert r.json()["version"] == 2
        assert r.json()["id"] != r1.json()["id"]

    def test_calculating_emits_a_domain_event(self, client, db_session: Session):
        """ZR-ENG-CLR-006 Section 20.2: refund.entitlement_calculated."""
        admin = _make_admin(db_session, email="refund-event-admin@test.com", role="super_admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="event1")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=admin_cookies,
        )
        case_id = r.json()["id"]
        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        entitlement_id = r.json()["id"]

        event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "refund.entitlement_calculated",
                DomainEvent.resource_type == "refund_entitlement",
                DomainEvent.resource_id == str(entitlement_id),
            )
        )
        assert event is not None
        assert event.payload["terminationCaseId"] == case_id

    def test_an_outsider_admin_cannot_calculate_the_entitlement(self, client, db_session: Session):
        admin = _make_admin(db_session, email="refund-owner-admin@test.com", role="admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="owner1")
        outsider = _make_admin(db_session, email="refund-outsider-admin@test.com", role="admin")

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=auth_admin_cookie(admin),
        )
        case_id = r.json()["id"]

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=auth_admin_cookie(outsider))
        assert r.status_code == 403, r.text

    def test_cannot_calculate_for_a_withdrawn_case(self, client, db_session: Session):
        admin = _make_admin(db_session, email="refund-withdrawn-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="withdrawn1")
        renter = _make_user(db_session, email="refund-withdrawn-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]
        client.post(f"/api/users/rentals/termination-cases/{case_id}/withdraw", cookies=auth_user_cookie(renter))

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=auth_admin_cookie(admin))
        assert r.status_code == 409, r.text


class TestListRefundEntitlementsForAdmin:
    """The admin-wide '/refund-entitlements' inbox -- the refund-entitlement
    counterpart to termination-cases' list_termination_cases_for_admin, same
    provider-ownership scoping."""

    def test_owner_sees_their_own_entitlement(self, client, db_session: Session):
        admin = _make_admin(db_session, email="refund-inbox-owner@test.com", role="admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="inboxowner")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=admin_cookies,
        )
        case_id = r.json()["id"]
        client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)

        r = client.get("/api/occupancy/refund-entitlements", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1
        assert r.json()[0]["terminationCaseId"] == case_id

    def test_outsider_admin_does_not_see_it(self, client, db_session: Session):
        admin = _make_admin(db_session, email="refund-inbox-owner2@test.com", role="admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="inboxowner2")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=admin_cookies,
        )
        case_id = r.json()["id"]
        client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)

        outsider = _make_admin(db_session, email="refund-inbox-outsider@test.com", role="admin")
        r = client.get("/api/occupancy/refund-entitlements", cookies=auth_admin_cookie(outsider))
        assert r.status_code == 200, r.text
        assert r.json() == []

    def test_super_admin_sees_every_entitlement(self, client, db_session: Session):
        owner = _make_admin(db_session, email="refund-inbox-owner3@test.com", role="admin")
        occupancy, _guest, _listing, _agreement = _make_active_occupancy(db_session, admin=owner, suffix="inboxowner3")
        owner_cookies = auth_admin_cookie(owner)
        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=owner_cookies,
        )
        case_id = r.json()["id"]
        client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=owner_cookies)

        super_admin = _make_admin(db_session, email="refund-inbox-super@test.com", role="super_admin")
        r = client.get("/api/occupancy/refund-entitlements", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1


class TestExecuteRefundEntitlement:
    """ZR-ENG-CLR-006 Section 15/16.1: executing an entitlement creates a
    real Section 5 RefundRequest per REFUNDABLE_UNEARNED_RENT line and
    auto-approves it (this simulated build has no separate money-movement
    step -- see crud/refund_entitlement.py's own docstring)."""

    def _open_case_with_unearned_rent(self, client, db_session: Session, *, suffix: str):
        admin = _make_admin(db_session, email=f"refund-exec-{suffix}@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix=suffix)
        future_obligation = Obligation(
            obligation_type="RENT", money_plane="OCCUPANCY", amount=500.0, currency="INR",
            due_date=date.today() + timedelta(days=20), status="PENDING", occupancy_id=occupancy.id,
        )
        db_session.add(future_obligation)
        db_session.commit()
        _pay_obligation(db_session, guest.id, future_obligation, 500.0, suffix=suffix)

        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=admin_cookies,
        )
        case_id = r.json()["id"]
        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        entitlement_id = r.json()["id"]
        return admin, admin_cookies, occupancy, future_obligation, case_id, entitlement_id

    def test_execute_creates_and_approves_a_refund_request(self, client, db_session: Session):
        _admin, admin_cookies, _occupancy, future_obligation, _case_id, entitlement_id = self._open_case_with_unearned_rent(
            client, db_session, suffix="exec1",
        )

        client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/approve", cookies=admin_cookies)
        r = client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/execute", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "EXECUTED"
        assert body["executedAt"] is not None

        unearned_line = next(item for item in body["lineItems"] if item["type"] == "REFUNDABLE_UNEARNED_RENT")
        assert unearned_line["refundRequestId"] is not None

        db_session.refresh(future_obligation)
        assert future_obligation.status == "REFUNDED"

    def test_execute_notifies_the_renter(self, client, db_session: Session):
        _admin, admin_cookies, occupancy, _future_obligation, _case_id, entitlement_id = self._open_case_with_unearned_rent(
            client, db_session, suffix="exec-notify",
        )
        renter = _make_user(db_session, email="refund-exec-notify-renter@test.com")
        occupancy.guest.user_account_id = renter.id
        db_session.commit()

        client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/approve", cookies=admin_cookies)
        r = client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/execute", cookies=admin_cookies)
        assert r.status_code == 200, r.text

        notification = db_session.scalar(
            select(Notification).where(
                Notification.notification_type == "refund_entitlement.executed",
                Notification.recipient_user_id == renter.id,
            )
        )
        assert notification is not None

    def test_execute_emits_a_domain_event(self, client, db_session: Session):
        """ZR-ENG-CLR-006 Section 20.2 -- see crud/refund_entitlement.py's own
        comment for why this build has no separate refund.failed event."""
        _admin, admin_cookies, _occupancy, _future_obligation, case_id, entitlement_id = self._open_case_with_unearned_rent(
            client, db_session, suffix="exec-event",
        )
        client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/approve", cookies=admin_cookies)
        r = client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/execute", cookies=admin_cookies)
        refund_request_id = next(item["refundRequestId"] for item in r.json()["lineItems"] if item["type"] == "REFUNDABLE_UNEARNED_RENT")

        event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "refund_entitlement.executed",
                DomainEvent.resource_type == "refund_entitlement",
                DomainEvent.resource_id == str(entitlement_id),
            )
        )
        assert event is not None
        assert event.payload["terminationCaseId"] == case_id

        submitted_event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "refund.submitted",
                DomainEvent.resource_type == "refund_request",
                DomainEvent.resource_id == str(refund_request_id),
            )
        )
        assert submitted_event is not None
        assert submitted_event.payload["terminationCaseId"] == case_id

    def test_executing_twice_on_the_same_entitlement_is_rejected(self, client, db_session: Session):
        _admin, admin_cookies, _occupancy, _future_obligation, _case_id, entitlement_id = self._open_case_with_unearned_rent(
            client, db_session, suffix="exec2",
        )
        client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/approve", cookies=admin_cookies)
        r1 = client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/execute", cookies=admin_cookies)
        assert r1.status_code == 200, r1.text

        r2 = client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/execute", cookies=admin_cookies)
        assert r2.status_code == 409, r2.text

    def test_a_stale_unexecuted_version_reuses_the_refund_a_newer_version_already_created(self, client, db_session: Session):
        """Two entitlement versions calculated back-to-back before either is
        executed both still point at the same then-unrefunded obligation.
        Executing the newer one first creates the real RefundRequest;
        executing the older, now-stale version afterwards must reuse that
        same request rather than attempt a second refund of an
        already-refunded obligation (AC-23)."""
        admin, admin_cookies, occupancy, _future_obligation, case_id, first_entitlement_id = self._open_case_with_unearned_rent(
            client, db_session, suffix="exec3",
        )

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        second_entitlement_id = r.json()["id"]
        assert r.json()["version"] == 2

        client.post(f"/api/occupancy/refund-entitlements/{second_entitlement_id}/approve", cookies=admin_cookies)
        r = client.post(f"/api/occupancy/refund-entitlements/{second_entitlement_id}/execute", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        newer_refund_id = next(item for item in r.json()["lineItems"] if item["type"] == "REFUNDABLE_UNEARNED_RENT")["refundRequestId"]

        client.post(f"/api/occupancy/refund-entitlements/{first_entitlement_id}/approve", cookies=admin_cookies)
        r = client.post(f"/api/occupancy/refund-entitlements/{first_entitlement_id}/execute", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        older_refund_id = next(item for item in r.json()["lineItems"] if item["type"] == "REFUNDABLE_UNEARNED_RENT")["refundRequestId"]
        assert older_refund_id == newer_refund_id

    def test_an_outsider_admin_cannot_execute(self, client, db_session: Session):
        _admin, admin_cookies, _occupancy, _future_obligation, _case_id, entitlement_id = self._open_case_with_unearned_rent(
            client, db_session, suffix="exec4",
        )
        outsider = _make_admin(db_session, email="refund-exec-outsider@test.com", role="admin")

        r = client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/execute", cookies=auth_admin_cookie(outsider))
        assert r.status_code == 403, r.text

    def test_executing_an_unapproved_entitlement_is_rejected(self, client, db_session: Session):
        """ZR-ENG-CLR-006 Section 16.1: CALCULATED must move to APPROVED
        before it can be executed -- see models/refund_entitlement.py:
        REFUND_ENTITLEMENT_STATUSES's own docstring for why this build
        requires the step explicitly rather than auto-approving."""
        _admin, admin_cookies, _occupancy, _future_obligation, _case_id, entitlement_id = self._open_case_with_unearned_rent(
            client, db_session, suffix="exec5",
        )

        r = client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/execute", cookies=admin_cookies)
        assert r.status_code == 409, r.text


class TestApproveRefundEntitlement:
    """ZR-ENG-CLR-006 Section 16.1/20.1 POST /refund-entitlements/{id}/approve."""

    def _open_case_with_unearned_rent(self, client, db_session: Session, *, suffix: str):
        return TestExecuteRefundEntitlement()._open_case_with_unearned_rent(client, db_session, suffix=suffix)

    def test_provider_owner_can_approve_and_it_unlocks_execution(self, client, db_session: Session):
        admin, admin_cookies, _occupancy, _future_obligation, _case_id, entitlement_id = self._open_case_with_unearned_rent(
            client, db_session, suffix="approve1",
        )

        r = client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/approve", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "APPROVED"
        assert body["approvedByAdminId"] == admin.id
        assert body["approvedAt"] is not None

        r = client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/execute", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "EXECUTED"

    def test_approving_twice_is_rejected(self, client, db_session: Session):
        _admin, admin_cookies, _occupancy, _future_obligation, _case_id, entitlement_id = self._open_case_with_unearned_rent(
            client, db_session, suffix="approve2",
        )
        client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/approve", cookies=admin_cookies)

        r = client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/approve", cookies=admin_cookies)
        assert r.status_code == 409, r.text

    def test_an_outsider_admin_cannot_approve(self, client, db_session: Session):
        _admin, admin_cookies, _occupancy, _future_obligation, _case_id, entitlement_id = self._open_case_with_unearned_rent(
            client, db_session, suffix="approve3",
        )
        outsider = _make_admin(db_session, email="refund-approve-outsider@test.com", role="admin")

        r = client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/approve", cookies=auth_admin_cookie(outsider))
        assert r.status_code == 403, r.text

    def test_approving_emits_a_domain_event(self, client, db_session: Session):
        _admin, admin_cookies, _occupancy, _future_obligation, case_id, entitlement_id = self._open_case_with_unearned_rent(
            client, db_session, suffix="approve4",
        )
        client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/approve", cookies=admin_cookies)

        event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "refund.approved",
                DomainEvent.resource_type == "refund_entitlement",
                DomainEvent.resource_id == str(entitlement_id),
            )
        )
        assert event is not None
        assert event.payload["terminationCaseId"] == case_id


class TestRenterViewsOwnRefundEntitlement:
    def test_renter_can_view_the_latest_entitlement_for_their_own_case(self, client, db_session: Session):
        admin = _make_admin(db_session, email="refund-renterview-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="renterview1")
        renter = _make_user(db_session, email="refund-renterview-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()
        admin_cookies = auth_admin_cookie(admin)
        renter_cookies = auth_user_cookie(renter)

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=admin_cookies,
        )
        case_id = r.json()["id"]
        client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)

        r = client.get(f"/api/users/rentals/termination-cases/{case_id}/refund-entitlement", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["version"] == 1

    def test_a_different_renter_cannot_view_it(self, client, db_session: Session):
        admin = _make_admin(db_session, email="refund-stranger-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="stranger1")
        owning_renter = _make_user(db_session, email="refund-stranger-owner@test.com")
        guest.user_account_id = owning_renter.id
        db_session.commit()
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=admin_cookies,
        )
        case_id = r.json()["id"]
        client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)

        stranger = _make_user(db_session, email="refund-stranger-stranger@test.com")
        r = client.get(f"/api/users/rentals/termination-cases/{case_id}/refund-entitlement", cookies=auth_user_cookie(stranger))
        assert r.status_code == 403, r.text

    def test_returns_404_before_any_calculation_exists(self, client, db_session: Session):
        admin = _make_admin(db_session, email="refund-nocalc-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="nocalc1")
        renter = _make_user(db_session, email="refund-nocalc-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=auth_admin_cookie(admin),
        )
        case_id = r.json()["id"]

        r = client.get(f"/api/users/rentals/termination-cases/{case_id}/refund-entitlement", cookies=auth_user_cookie(renter))
        assert r.status_code == 404, r.text


class TestPlatformFeeReversal:
    """ZR-ENG-CLR-006 Section 13/AC-15: 'Host fee recalculated on rent
    ultimately earned' -- reversing the platform fee run_payout already took
    on rent that later gets refunded through a termination case, once that
    rent had already gone through a COMPLETED payout."""

    def test_fee_is_reversed_when_a_paid_out_obligation_is_later_refunded(self, client, db_session: Session):
        admin = _make_admin(db_session, email="refund-fee-admin@test.com", role="super_admin")
        occupancy, guest, _listing, agreement = _make_active_occupancy(db_session, admin=admin, suffix="fee1")
        admin_cookies = auth_admin_cookie(admin)

        future_obligation = Obligation(
            obligation_type="RENT", money_plane="OCCUPANCY", amount=1000.0, currency="INR",
            due_date=date.today() + timedelta(days=20), status="PAID", occupancy_id=occupancy.id,
        )
        db_session.add(future_obligation)
        db_session.commit()
        _pay_obligation(db_session, guest.id, future_obligation, 1000.0, suffix="fee1")

        party_id = agreement.offer.listing.room.property.owner_party_id
        db_session.add(PayoutBeneficiary(
            party_id=party_id, account_holder_name="Test Landlord", bank_name="Test Bank",
            account_number_last4="1234", bank_identifier_code="TEST0123456", status="VERIFIED",
            verified_at=datetime.now(timezone.utc),
        ))
        db_session.commit()

        period_key = date.today().strftime("%Y-%m")
        r = client.post("/api/finance/payouts/run", json={"partyId": party_id, "periodKey": period_key}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PAID"

        policy = db_session.query(MarketPolicyPack).filter_by(jurisdiction_code="IN").one()
        expected_fee_reversal = round(1000.0 * float(policy.platform_fee_rate), 2)

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=admin_cookies,
        )
        case_id = r.json()["id"]
        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        entitlement_id = r.json()["id"]

        client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/approve", cookies=admin_cookies)
        r = client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/execute", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        refund_request_id = next(
            item["refundRequestId"] for item in r.json()["lineItems"] if item["type"] == "REFUNDABLE_UNEARNED_RENT"
        )

        entry = db_session.scalar(
            select(LedgerEntry).where(
                LedgerEntry.source_type == "refund_request", LedgerEntry.source_id == str(refund_request_id),
                LedgerEntry.description.like("Platform fee reversed%"),
            )
        )
        assert entry is not None
        assert round(float(entry.amount), 2) == expected_fee_reversal

    def test_no_reversal_when_the_obligation_was_never_paid_out(self, client, db_session: Session):
        """The ordinary case -- most refunded obligations never went through
        run_payout at all, so there's nothing to reverse."""
        admin = _make_admin(db_session, email="refund-fee-none-admin@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix="fee-none")
        admin_cookies = auth_admin_cookie(admin)

        future_obligation = Obligation(
            obligation_type="RENT", money_plane="OCCUPANCY", amount=500.0, currency="INR",
            due_date=date.today() + timedelta(days=20), status="PENDING", occupancy_id=occupancy.id,
        )
        db_session.add(future_obligation)
        db_session.commit()
        _pay_obligation(db_session, guest.id, future_obligation, 500.0, suffix="fee-none")

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=admin_cookies,
        )
        case_id = r.json()["id"]
        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        entitlement_id = r.json()["id"]

        client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/approve", cookies=admin_cookies)
        r = client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/execute", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        refund_request_id = next(
            item["refundRequestId"] for item in r.json()["lineItems"] if item["type"] == "REFUNDABLE_UNEARNED_RENT"
        )

        entry = db_session.scalar(
            select(LedgerEntry).where(
                LedgerEntry.source_type == "refund_request", LedgerEntry.source_id == str(refund_request_id),
                LedgerEntry.description.like("Platform fee reversed%"),
            )
        )
        assert entry is None


class TestTribunalLiability:
    """ZR-ENG-CLR-006 Section 11.1 TRIBUNAL_OR_COURT_DETERMINED: a Super
    Admin's own entry (never a computed formula) becomes the calculation's
    NOTICE_LIABILITY line, reducing net_refund below gross_refundable, and
    is what execute_refund_entitlement actually pays out less of."""

    def _open_case_with_unearned_rent(self, client, db_session: Session, *, suffix: str, amount: float = 1000.0):
        admin = _make_admin(db_session, email=f"tribunal-{suffix}@test.com", role="super_admin")
        occupancy, guest, _listing, _agreement = _make_active_occupancy(db_session, admin=admin, suffix=suffix)
        admin_cookies = auth_admin_cookie(admin)

        future_obligation = Obligation(
            obligation_type="RENT", money_plane="OCCUPANCY", amount=amount, currency="INR",
            due_date=date.today() + timedelta(days=20), status="PENDING", occupancy_id=occupancy.id,
        )
        db_session.add(future_obligation)
        db_session.commit()
        _pay_obligation(db_session, guest.id, future_obligation, amount, suffix=suffix)

        r = client.post(
            f"/api/occupancy/{occupancy.id}/termination-cases",
            json={"causeCode": "HOST_FAULT_OR_NONPERFORMANCE"}, cookies=admin_cookies,
        )
        case_id = r.json()["id"]
        return admin, admin_cookies, occupancy, future_obligation, case_id

    def test_setting_liability_requires_super_admin_and_a_reason(self, client, db_session: Session):
        _admin, admin_cookies, _occupancy, _obligation, case_id = self._open_case_with_unearned_rent(client, db_session, suffix="set1")

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/tribunal-liability",
            json={"amount": 200.0, "reason": ""}, cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

        regular_admin = _make_admin(db_session, email="tribunal-regular@test.com", role="admin")
        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/tribunal-liability",
            json={"amount": 200.0, "reason": "trying anyway"}, cookies=auth_admin_cookie(regular_admin),
        )
        assert r.status_code == 403, r.text

    def test_liability_reduces_net_refund_but_not_gross_refundable(self, client, db_session: Session):
        admin, admin_cookies, _occupancy, _obligation, case_id = self._open_case_with_unearned_rent(client, db_session, suffix="set2")

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/tribunal-liability",
            json={"amount": 300.0, "reason": "Tribunal ordered a 300 liability for early vacancy"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert float(r.json()["tribunalLiabilityAmount"]) == 300.0

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        assert float(body["grossRefundable"]) == 1000.0
        assert float(body["netRefund"]) == 700.0
        notice_line = next(item for item in body["lineItems"] if item["type"] == "NOTICE_LIABILITY")
        assert float(notice_line["amount"]) == 300.0

    def test_execute_pays_out_only_the_net_amount(self, client, db_session: Session):
        admin, admin_cookies, _occupancy, future_obligation, case_id = self._open_case_with_unearned_rent(client, db_session, suffix="set3")
        client.post(
            f"/api/occupancy/termination-cases/{case_id}/tribunal-liability",
            json={"amount": 300.0, "reason": "Tribunal ordered liability"},
            cookies=admin_cookies,
        )
        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        entitlement_id = r.json()["id"]

        client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/approve", cookies=admin_cookies)
        r = client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/execute", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        unearned_line = next(item for item in r.json()["lineItems"] if item["type"] == "REFUNDABLE_UNEARNED_RENT")
        refund_id = unearned_line["refundRequestId"]
        assert refund_id is not None

        from app.models.finance import RefundRequest
        refund = db_session.get(RefundRequest, refund_id)
        assert float(refund.amount) == 700.0

    def test_liability_exceeding_gross_refundable_never_pays_a_negative_amount(self, client, db_session: Session):
        admin, admin_cookies, _occupancy, _obligation, case_id = self._open_case_with_unearned_rent(client, db_session, suffix="set4")
        client.post(
            f"/api/occupancy/termination-cases/{case_id}/tribunal-liability",
            json={"amount": 5000.0, "reason": "Tribunal ordered a large liability"},
            cookies=admin_cookies,
        )

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert float(r.json()["netRefund"]) == 0.0

    def test_tribunal_liability_calculate_and_execute_are_all_audit_logged(self, client, db_session: Session):
        """These are the highest-stakes admin actions in the whole engine --
        a Super Admin override, a versioned money calculation and an actual
        payment-execution trigger -- so each needs its own audit trail entry,
        not just the generic domain-event outbox."""
        admin, admin_cookies, _occupancy, _obligation, case_id = self._open_case_with_unearned_rent(client, db_session, suffix="audit1")

        r = client.post(
            f"/api/occupancy/termination-cases/{case_id}/tribunal-liability",
            json={"amount": 300.0, "reason": "Tribunal ordered a 300 liability"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        audit = db_session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "termination_case.tribunal_liability", AuditEvent.resource_id == str(case_id),
            )
        )
        assert audit is not None
        assert audit.reason == "Tribunal ordered a 300 liability"

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        entitlement_id = r.json()["id"]
        audit = db_session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "refund_entitlement.calculate", AuditEvent.resource_id == str(entitlement_id),
            )
        )
        assert audit is not None
        assert audit.object_version == "1"

        r = client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/approve", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        audit = db_session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "refund_entitlement.approve", AuditEvent.resource_id == str(entitlement_id),
            )
        )
        assert audit is not None
        assert audit.after_state == "APPROVED"

        r = client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/execute", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        audit = db_session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "refund_entitlement.execute", AuditEvent.resource_id == str(entitlement_id),
            )
        )
        assert audit is not None
        assert audit.after_state == "EXECUTED"

    def test_no_liability_leaves_the_notice_liability_line_at_zero(self, client, db_session: Session):
        _admin, admin_cookies, _occupancy, _obligation, case_id = self._open_case_with_unearned_rent(client, db_session, suffix="set5")

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        notice_line = next(item for item in r.json()["lineItems"] if item["type"] == "NOTICE_LIABILITY")
        assert float(notice_line["amount"]) == 0.0
        assert float(r.json()["grossRefundable"]) == float(r.json()["netRefund"])


class TestLiabilityModels:
    """ZR-ENG-CLR-006 Section 11.1/AC-12: the per-market-pack liability-model
    dispatch (crud/refund_entitlement.py:_compute_policy_liability). Each
    test mutates the shared IN MarketPolicyPack row directly -- db_session is
    transactional per test (tests/conftest.py), so this never leaks into
    other tests -- and adds the PaymentSchedule row _make_active_occupancy's
    own fixture doesn't create (it builds the agreement directly, bypassing
    crud/leasing.py:create_agreement's normal schedule-creation step)."""

    def _set_liability_model(self, db: Session, *, model: str, break_fee_multiple: float = 0.0, cap_multiple: float | None = None) -> None:
        policy = db.query(MarketPolicyPack).filter_by(jurisdiction_code="IN").one()
        policy.termination_liability_model = model
        policy.termination_break_fee_rent_multiple = break_fee_multiple
        policy.termination_liability_cap_rent_multiple = cap_multiple
        db.commit()

    def _open_case_with_schedule(self, client, db_session: Session, *, suffix: str, monthly_rent: float = 1000.0):
        """RENTER_ORDINARY_EARLY_EXIT, not HOST_FAULT_OR_NONPERFORMANCE --
        _compute_policy_liability always zero-liabilities the latter
        regardless of the configured model (a Host-fault cause is always
        the renter's own zero-liability protection, per spec), so exercising
        STATUTORY_BREAK_FEE/CAPPED_COMPENSATION/ACTUAL_REASONABLE_LOSS needs
        the one cause those models are actually meant to apply to."""
        admin = _make_admin(db_session, email=f"liability-{suffix}@test.com", role="super_admin")
        occupancy, guest, _listing, agreement = _make_active_occupancy(db_session, admin=admin, suffix=suffix, monthly_rent=monthly_rent)
        db_session.add(PaymentSchedule(
            agreement_id=agreement.id, cadence="MONTHLY", amount=monthly_rent, first_due=date.today(), anchor_day=date.today().day,
            status="ACTIVE",
        ))
        db_session.commit()

        # RENTER_ORDINARY_EARLY_EXIT resolves to today + the market pack's
        # termination_notice_days (default 30, unchanged here) -- due_date
        # must fall after that to land as REFUNDABLE_UNEARNED_RENT rather
        # than EARNED_RENT.
        future_obligation = Obligation(
            obligation_type="RENT", money_plane="OCCUPANCY", amount=monthly_rent, currency="INR",
            due_date=date.today() + timedelta(days=40), status="PENDING", occupancy_id=occupancy.id,
        )
        db_session.add(future_obligation)
        db_session.commit()
        _pay_obligation(db_session, guest.id, future_obligation, monthly_rent, suffix=suffix)

        renter = _make_user(db_session, email=f"liability-{suffix}-renter@test.com")
        guest.user_account_id = renter.id
        db_session.commit()

        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT"}, cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]
        return admin, admin_cookies, occupancy, case_id

    def test_statutory_break_fee_charges_a_rent_multiple(self, client, db_session: Session):
        self._set_liability_model(db_session, model="STATUTORY_BREAK_FEE", break_fee_multiple=0.5)
        _admin, admin_cookies, _occupancy, case_id = self._open_case_with_schedule(client, db_session, suffix="sbf1", monthly_rent=1000.0)

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        notice_line = next(item for item in body["lineItems"] if item["type"] == "NOTICE_LIABILITY")
        assert float(notice_line["amount"]) == 500.0
        assert float(body["grossRefundable"]) == 1000.0
        assert float(body["netRefund"]) == 500.0

    def test_capped_compensation_caps_the_raw_break_fee(self, client, db_session: Session):
        # A 1x-rent break fee configured, but capped at 0.25x -- the cap wins.
        self._set_liability_model(db_session, model="CAPPED_COMPENSATION", break_fee_multiple=1.0, cap_multiple=0.25)
        _admin, admin_cookies, _occupancy, case_id = self._open_case_with_schedule(client, db_session, suffix="cap1", monthly_rent=1000.0)

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        notice_line = next(item for item in r.json()["lineItems"] if item["type"] == "NOTICE_LIABILITY")
        assert float(notice_line["amount"]) == 250.0

    def test_zero_liability_model_charges_nothing_even_with_a_configured_multiple(self, client, db_session: Session):
        self._set_liability_model(db_session, model="ZERO_LIABILITY", break_fee_multiple=1.0)
        _admin, admin_cookies, _occupancy, case_id = self._open_case_with_schedule(client, db_session, suffix="zero1")

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        notice_line = next(item for item in r.json()["lineItems"] if item["type"] == "NOTICE_LIABILITY")
        assert float(notice_line["amount"]) == 0.0

    def test_break_fee_bands_taper_by_elapsed_months(self, client, db_session: Session):
        """Section 6 gap: the fee no longer has to be one flat multiple --
        a market pack can now taper it down by how far into the lease the
        renter got before leaving."""
        policy = db_session.query(MarketPolicyPack).filter_by(jurisdiction_code="IN").one()
        policy.termination_liability_model = "STATUTORY_BREAK_FEE"
        policy.termination_break_fee_rent_multiple = 1.0  # flat fallback -- must be ignored once bands are set
        policy.termination_break_fee_bands = [
            {"maxElapsedMonths": 1, "multiple": 1.0},
            {"maxElapsedMonths": 6, "multiple": 0.5},
        ]
        db_session.commit()
        _admin, admin_cookies, _occupancy, case_id = self._open_case_with_schedule(client, db_session, suffix="band1", monthly_rent=1000.0)

        # 0 whole months elapsed (move_in_date == today) -- lands in the first band.
        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        notice_line = next(item for item in r.json()["lineItems"] if item["type"] == "NOTICE_LIABILITY")
        assert float(notice_line["amount"]) == 1000.0  # 1.0x, not the flat 1.0x's own coincidental match

    def test_break_fee_bands_taper_to_zero_beyond_every_band(self, client, db_session: Session):
        policy = db_session.query(MarketPolicyPack).filter_by(jurisdiction_code="IN").one()
        policy.termination_liability_model = "STATUTORY_BREAK_FEE"
        policy.termination_break_fee_rent_multiple = 1.0
        policy.termination_break_fee_bands = [{"maxElapsedMonths": 1, "multiple": 1.0}]
        db_session.commit()
        admin, admin_cookies, occupancy, case_id = self._open_case_with_schedule(client, db_session, suffix="band2", monthly_rent=1000.0)

        # Push move_in_date far enough into the past that elapsed months
        # exceeds the only configured band's own ceiling.
        occupancy.move_in_date = date.today() - timedelta(days=365)
        db_session.commit()

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        notice_line = next(item for item in r.json()["lineItems"] if item["type"] == "NOTICE_LIABILITY")
        assert float(notice_line["amount"]) == 0.0

    def test_no_bands_configured_falls_back_to_the_flat_multiple(self, client, db_session: Session):
        self._set_liability_model(db_session, model="STATUTORY_BREAK_FEE", break_fee_multiple=0.5)
        _admin, admin_cookies, _occupancy, case_id = self._open_case_with_schedule(client, db_session, suffix="band3", monthly_rent=1000.0)

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        notice_line = next(item for item in r.json()["lineItems"] if item["type"] == "NOTICE_LIABILITY")
        assert float(notice_line["amount"]) == 500.0

    def test_actual_reasonable_loss_charges_recorded_reletting_costs(self, client, db_session: Session):
        self._set_liability_model(db_session, model="ACTUAL_REASONABLE_LOSS")
        admin, admin_cookies, occupancy, case_id = self._open_case_with_schedule(client, db_session, suffix="arl1")

        case = termination_crud.get_termination_case_or_404(db_session, case_id)
        termination_crud.record_mitigation(
            db_session, case, admin, MitigationRecordCreate(reasonable_reletting_costs=150.0, notes="Advertising costs"),
        )

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        notice_line = next(item for item in body["lineItems"] if item["type"] == "NOTICE_LIABILITY")
        assert float(notice_line["amount"]) == 150.0
        mitigation_line = next(item for item in body["lineItems"] if item["type"] == "MITIGATION_CREDIT")
        assert float(mitigation_line["amount"]) == 0.0

    def test_actual_reasonable_loss_overlap_guard_credits_replacement_rent(self, client, db_session: Session):
        """AC-13/AC-14: a Host who re-lets promptly cannot also collect the
        full reasonable-loss charge from the departing renter -- replacement
        rent already recovered reduces (never below zero) the same amount."""
        self._set_liability_model(db_session, model="ACTUAL_REASONABLE_LOSS")
        admin, admin_cookies, occupancy, case_id = self._open_case_with_schedule(client, db_session, suffix="arl2")

        case = termination_crud.get_termination_case_or_404(db_session, case_id)
        termination_crud.record_mitigation(
            db_session, case, admin,
            MitigationRecordCreate(reasonable_reletting_costs=150.0, replacement_rent_amount=150.0, notes="Re-let within a week"),
        )

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        notice_line = next(item for item in body["lineItems"] if item["type"] == "NOTICE_LIABILITY")
        assert float(notice_line["amount"]) == 0.0  # fully mitigated
        mitigation_line = next(item for item in body["lineItems"] if item["type"] == "MITIGATION_CREDIT")
        assert float(mitigation_line["amount"]) == 150.0
        assert "overlap_guard" in mitigation_line["basisNote"]

    def test_tribunal_liability_supersedes_the_modeled_estimate_not_adds_to_it(self, client, db_session: Session):
        """A Super Admin's own real determination replaces the policy
        model's own estimate rather than stacking on top of it -- otherwise
        the renter would be charged for the same early-termination event
        twice."""
        self._set_liability_model(db_session, model="STATUTORY_BREAK_FEE", break_fee_multiple=0.5)
        admin, admin_cookies, _occupancy, case_id = self._open_case_with_schedule(client, db_session, suffix="sup1", monthly_rent=1000.0)

        client.post(
            f"/api/occupancy/termination-cases/{case_id}/tribunal-liability",
            json={"amount": 100.0, "reason": "Tribunal determined a lower amount than the market pack's own formula"},
            cookies=admin_cookies,
        )
        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        notice_line = next(item for item in r.json()["lineItems"] if item["type"] == "NOTICE_LIABILITY")
        assert float(notice_line["amount"]) == 100.0  # not 500 (the STATUTORY_BREAK_FEE estimate) + 100
