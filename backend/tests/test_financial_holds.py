"""ZR-ENG-CLR-005 Section 15.1/19.2: run_reconciliation creates a real,
queryable, resolvable FinancialHold row for each failed check (not just a
plain string in ReconciliationRun.mismatches), and the new resolve endpoint
lets a super admin close one out. Reuses the exact "untracked allocation"
scenario from test_reconciliation_ledger.py, which is already proven to
trigger the LEDGER_ALLOCATION_MISMATCH check.

TestManualOperationalHold covers Section 6.4's admin console action this
build never had until now: "place ... authorized operational hold" -- every
FinancialHold before this was system-generated (reconciliation, negative
balance); create_financial_hold is the create half, and it actually gates
run_payout the same way the beneficiary/authority/habitability checks do."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import FinancialHold, Obligation
from app.models.guest import Guest
from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


def _trigger_a_ledger_allocation_mismatch(client, db_session: Session, admin_cookies: dict) -> None:
    """Same gap as test_reconciliation_ledger.py: a bare Obligation with no
    agreement/occupancy can't have its party resolved, so confirm_payment's
    ledger wiring silently skips posting an entry for it, even though the
    PaymentAllocation itself is created normally."""
    guest = Guest(id="G-HOLD-GAP", name="Renter", email="hold-gap@test.com", joined_at=date.today())
    db_session.add(guest)
    obligation = Obligation(
        obligation_type="RENT", money_plane="OCCUPANCY", amount=300.0, currency="INR",
        due_date=date.today(), status="PENDING",
    )
    db_session.add(obligation)
    db_session.commit()

    r = client.post(
        "/api/finance/payments",
        json={"guestId": guest.id, "amount": 300.0, "currency": "INR", "idempotencyKey": "hold-gap-1"},
        cookies=admin_cookies,
    )
    assert r.status_code == 201, r.text
    payment_id = r.json()["id"]
    r = client.post(
        f"/api/finance/payments/{payment_id}/confirm",
        json={"allocations": [{"obligationId": obligation.id, "amount": 300.0}]},
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text


class TestReconciliationCreatesFinancialHolds:
    def test_failed_check_creates_an_open_hold_with_correct_reason_and_severity(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="hold-super1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        _trigger_a_ledger_allocation_mismatch(client, db_session, admin_cookies)

        r = client.post("/api/finance/reconciliation/run", cookies=admin_cookies)
        assert r.status_code == 200, r.text

        hold = db_session.scalar(select(FinancialHold).where(FinancialHold.reason_code == "LEDGER_ALLOCATION_MISMATCH"))
        assert hold is not None
        assert hold.severity == "HIGH"
        assert hold.status == "OPEN"
        assert hold.source_type == "reconciliation_run"

    def test_clean_reconciliation_creates_no_holds(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="hold-super2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)

        r = client.post("/api/finance/reconciliation/run", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CLEAN"
        assert db_session.scalars(select(FinancialHold)).all() == []


class TestFinancialHoldListingAndResolution:
    def test_plain_admin_cannot_list_or_resolve_holds(self, client, db_session: Session):
        plain_admin = _make_admin(db_session, email="hold-plain@test.com", role="admin")
        plain_cookies = auth_admin_cookie(plain_admin)

        r = client.get("/api/finance/financial-holds", cookies=plain_cookies)
        assert r.status_code == 403, r.text

        r = client.post("/api/finance/financial-holds/1/resolve", json={"notes": "nope"}, cookies=plain_cookies)
        assert r.status_code == 403, r.text

    def test_super_admin_can_list_and_resolve_a_hold(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="hold-super3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        _trigger_a_ledger_allocation_mismatch(client, db_session, admin_cookies)
        assert client.post("/api/finance/reconciliation/run", cookies=admin_cookies).status_code == 200

        r = client.get("/api/finance/financial-holds", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        holds = r.json()
        assert len(holds) == 1
        hold_id = holds[0]["id"]
        assert holds[0]["status"] == "OPEN"

        r = client.get("/api/finance/financial-holds?status=OPEN", cookies=admin_cookies)
        assert len(r.json()) == 1

        r = client.post(
            f"/api/finance/financial-holds/{hold_id}/resolve", json={"notes": "Investigated, was a test fixture gap"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        resolved = r.json()
        assert resolved["status"] == "RESOLVED"
        assert resolved["resolutionNotes"] == "Investigated, was a test fixture gap"
        assert resolved["resolvedByAdminId"] == super_admin.id

        # Resolving an already-resolved hold is rejected, not silently re-accepted.
        r = client.post(f"/api/finance/financial-holds/{hold_id}/resolve", json={"notes": "again"}, cookies=admin_cookies)
        assert r.status_code == 409, r.text

        r = client.get("/api/finance/financial-holds?status=OPEN", cookies=admin_cookies)
        assert len(r.json()) == 0


class TestManualOperationalHold:
    def test_super_admin_can_place_a_hold_that_blocks_payout(self, client, db_session: Session):
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="manualhold1", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "manualhold1-pay"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]
        client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 1000.0}]},
            cookies=admin_cookies,
        )

        r = client.post(
            "/api/finance/financial-holds",
            json={"partyId": party_id, "description": "Fraud review in progress"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "OPEN"
        assert body["reasonCode"] == "MANUAL_OPERATIONAL_HOLD"
        assert body["sourceType"] == "party"
        assert body["sourceId"] == str(party_id)
        hold_id = body["id"]

        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        payout = r.json()
        assert payout["status"] == "HELD"
        assert "operational hold" in payout["holdReason"].lower()
        assert "Fraud review in progress" in payout["holdReason"]

        # Resolving the hold, then retrying, lets the payout through.
        client.post(f"/api/finance/financial-holds/{hold_id}/resolve", json={"notes": "Cleared"}, cookies=admin_cookies)
        r = client.post(f"/api/finance/payouts/{payout['id']}/retry", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PAID"

    def test_a_second_open_hold_for_the_same_party_is_rejected(self, client, db_session: Session):
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="manualhold2", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/financial-holds", json={"partyId": party_id, "description": "First hold"}, cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text

        r = client.post(
            "/api/finance/financial-holds", json={"partyId": party_id, "description": "Second hold"}, cookies=admin_cookies,
        )
        assert r.status_code == 409, r.text

    def test_a_plain_admin_cannot_place_a_hold(self, client, db_session: Session):
        _obligation, _admin, _guest, party_id = _make_provider_rent_obligation(db_session, suffix="manualhold3", amount=500.0)
        plain_admin = _make_admin(db_session, email="manualhold3-plain@test.com", role="admin")

        r = client.post(
            "/api/finance/financial-holds", json={"partyId": party_id, "description": "trying anyway"},
            cookies=auth_admin_cookie(plain_admin),
        )
        assert r.status_code == 403, r.text

    def test_an_open_hold_also_blocks_approving_a_refund(self, client, db_session: Session):
        """Section 10 gap: this hold already blocked payout -- it previously
        did nothing to a refund draining the exact same party's balance."""
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="manualhold4", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "manualhold4-pay"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]
        client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_cookies,
        )

        r = client.post(
            "/api/finance/financial-holds",
            json={"partyId": party_id, "description": "Fraud review in progress"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        hold_id = r.json()["id"]

        r = client.post(
            "/api/finance/refunds",
            json={
                "paymentId": payment_id, "obligationId": obligation.id, "amount": 500.0, "reason": "test",
                "idempotencyKey": "manualhold4-refund",
            },
            cookies=admin_cookies,
        )
        refund_id = r.json()["id"]
        r = client.post(f"/api/finance/refunds/{refund_id}/decide", json={"approve": True}, cookies=admin_cookies)
        assert r.status_code == 409, r.text
        assert "operational hold" in r.json()["detail"].lower()

        client.post(f"/api/finance/financial-holds/{hold_id}/resolve", json={"notes": "Cleared"}, cookies=admin_cookies)
        r = client.post(f"/api/finance/refunds/{refund_id}/decide", json={"approve": True}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
