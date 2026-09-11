"""ZR-ENG-CLR-005 Section 15.1/19.2: run_reconciliation creates a real,
queryable, resolvable FinancialHold row for each failed check (not just a
plain string in ReconciliationRun.mismatches), and the new resolve endpoint
lets a super admin close one out. Reuses the exact "untracked allocation"
scenario from test_reconciliation_ledger.py, which is already proven to
trigger the LEDGER_ALLOCATION_MISMATCH check."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import FinancialHold, Obligation
from app.models.guest import Guest
from tests.conftest import _make_admin, auth_admin_cookie


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
