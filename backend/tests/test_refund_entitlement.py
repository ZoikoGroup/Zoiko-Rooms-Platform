"""ZR-ENG-CLR-006 Section 12: the Refund Entitlement Calculation Engine.
Only the two real line types this build can compute for real (EARNED_RENT,
REFUNDABLE_UNEARNED_RENT) ever carry a nonzero amount -- every other named
Section 12.2 line item (notice liability beyond ordinary rent, mitigation,
renter fee, tax, other credit) is always zero (see
models/refund_entitlement.py's own module docstring for exactly why)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from sqlalchemy import select

from app.crud import refund_entitlement as refund_entitlement_crud
from app.crud import termination as termination_crud
from app.models.domain_event import DomainEvent
from app.models.finance import LedgerEntry, Obligation, PaymentAllocation, PayoutBeneficiary, SimulatedPayment
from app.models.market_policy import MarketPolicyPack
from app.models.notification import Notification
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
        comment for why this build emits one 'executed' event rather than
        separate submitted/settled ones."""
        _admin, admin_cookies, _occupancy, _future_obligation, case_id, entitlement_id = self._open_case_with_unearned_rent(
            client, db_session, suffix="exec-event",
        )
        client.post(f"/api/occupancy/refund-entitlements/{entitlement_id}/execute", cookies=admin_cookies)

        event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "refund_entitlement.executed",
                DomainEvent.resource_type == "refund_entitlement",
                DomainEvent.resource_id == str(entitlement_id),
            )
        )
        assert event is not None
        assert event.payload["terminationCaseId"] == case_id

    def test_executing_twice_on_the_same_entitlement_is_rejected(self, client, db_session: Session):
        _admin, admin_cookies, _occupancy, _future_obligation, _case_id, entitlement_id = self._open_case_with_unearned_rent(
            client, db_session, suffix="exec2",
        )
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

        r = client.post(f"/api/occupancy/refund-entitlements/{second_entitlement_id}/execute", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        newer_refund_id = next(item for item in r.json()["lineItems"] if item["type"] == "REFUNDABLE_UNEARNED_RENT")["refundRequestId"]

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
            account_number_last4="1234", ifsc_code="TEST0123456", status="VERIFIED",
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

    def test_no_liability_leaves_the_notice_liability_line_at_zero(self, client, db_session: Session):
        _admin, admin_cookies, _occupancy, _obligation, case_id = self._open_case_with_unearned_rent(client, db_session, suffix="set5")

        r = client.post(f"/api/occupancy/termination-cases/{case_id}/calculate-refund", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        notice_line = next(item for item in r.json()["lineItems"] if item["type"] == "NOTICE_LIABILITY")
        assert float(notice_line["amount"]) == 0.0
        assert float(r.json()["grossRefundable"]) == float(r.json()["netRefund"])
