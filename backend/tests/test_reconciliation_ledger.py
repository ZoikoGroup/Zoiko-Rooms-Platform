"""ZR-ENG-CLR-005 Section 19: run_reconciliation's two ledger-aware checks --
a global trial balance (every LedgerEntry debits and credits the same
amount, so summed across all accounts it must always net to zero) and a
cross-check that every positive PaymentAllocation has a matching ledger
entry. The second one is the more interesting case: it proves reconciliation
actually surfaces the real, known gap where confirm_payment can't resolve a
provider party (a bare obligation with no agreement/occupancy) and so
silently skips posting a ledger entry for that allocation."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from sqlalchemy.orm import Session

from app.crud import payment_provider as payment_provider_crud
from app.models.finance import Obligation
from app.models.guest import Guest
from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


class TestReconciliationIsCleanAfterNormalPayment:
    def test_clean_status_with_ledger_totals_present(self, client, db_session: Session):
        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="reconclean1", amount=400.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 400.0, "currency": "INR", "idempotencyKey": "recon-clean-1"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 400.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post("/api/finance/reconciliation/run", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        run = r.json()
        assert run["status"] == "CLEAN"
        assert run["totals"]["ledgerTrialBalance"] == 0.0
        assert run["totals"]["totalLedgerCollected"] >= 400.0


class TestReconciliationFlagsUntrackedAllocation:
    def test_flags_a_paid_allocation_that_never_got_a_ledger_entry(self, client, db_session: Session):
        # A bare Obligation with no agreement/occupancy -- _obligation_party_id
        # can't resolve a party for it, so confirm_payment's ledger wiring
        # silently skips posting an entry (by design: an unresolvable party
        # must never turn an otherwise-valid confirm into an error), even
        # though the PaymentAllocation itself is still created normally.
        guest = Guest(id="G-RECON-GAP", name="Renter", email="recon-gap@test.com", joined_at=date.today())
        db_session.add(guest)
        obligation = Obligation(
            obligation_type="RENT", money_plane="OCCUPANCY", amount=250.0, currency="INR",
            due_date=date.today(), status="PENDING",
        )
        db_session.add(obligation)
        db_session.commit()

        admin = _make_admin(db_session, email="recon-gap-admin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 250.0, "currency": "INR", "idempotencyKey": "recon-gap-1"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 250.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post("/api/finance/reconciliation/run", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        run = r.json()
        assert run["status"] == "DISCREPANCIES_FOUND"
        assert any("Ledger-recorded collections" in m for m in run["mismatches"])


class TestReconciliationChecksAgainstStripeItself:
    """Section 5 gap: every check above only ever compares this platform's
    own internal tables against each other -- none of them would ever catch
    a real Stripe-side discrepancy. Stripe isn't configured in tests, so
    is_configured()/retrieve_payment_intent are mocked directly (the module
    boundary app/services/stripe_client.py is deliberately designed around,
    per its own docstring) rather than hitting the real stripe package."""

    def test_a_stripe_amount_mismatch_is_flagged_as_critical(self, client, db_session: Session):
        from app.core.config import settings

        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="reconpsp1", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)
        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")

        payment = payment_provider_crud.renter_pay_obligation(db_session, guest, obligation, method_class="CARD")
        assert payment.status == "SUCCEEDED"

        with (
            patch("app.crud.finance.stripe_client.is_configured", return_value=True),
            patch("app.crud.finance.stripe_client.retrieve_payment_intent", return_value={"amount_received": 40000, "currency": "inr", "status": "succeeded"}),
        ):
            r = client.post("/api/finance/reconciliation/run", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        run = r.json()
        assert run["status"] == "DISCREPANCIES_FOUND"
        assert any("Stripe" in m for m in run["mismatches"])
        assert run["totals"]["pspTransactionsChecked"] == 1

    def test_matching_stripe_amounts_stay_clean(self, client, db_session: Session):
        from app.core.config import settings

        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="reconpsp2", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)
        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")

        payment = payment_provider_crud.renter_pay_obligation(db_session, guest, obligation, method_class="CARD")
        assert payment.status == "SUCCEEDED"

        with (
            patch("app.crud.finance.stripe_client.is_configured", return_value=True),
            patch("app.crud.finance.stripe_client.retrieve_payment_intent", return_value={"amount_received": 50000, "currency": "inr", "status": "succeeded"}),
        ):
            r = client.post("/api/finance/reconciliation/run", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        run = r.json()
        assert run["status"] == "CLEAN"
        assert run["totals"]["pspTransactionsChecked"] == 1

    def test_not_configured_skips_the_psp_check_entirely(self, client, db_session: Session):
        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="reconpsp3", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "recon-psp-3"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post("/api/finance/reconciliation/run", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["totals"]["pspTransactionsChecked"] == 0
