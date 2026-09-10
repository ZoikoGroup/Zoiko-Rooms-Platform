"""ZR-ENG-CLR-005 Section 20/AC-32: opening a CHARGEBACK dispute requires a
specific payment/obligation/amount and places a FinancialHold ("do not
assume renter wins"); resolving it with chargeback_outcome="LOST" reverses
the original collection in the ledger exactly like an approved refund does
(including the negative-balance-hold check from increment 7), while "WON"
leaves the ledger untouched -- either way the CHARGEBACK_OPENED hold closes."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import FinancialHold, LedgerEntry
from tests.conftest import auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


def _confirm_a_payment(client, db_session: Session, *, suffix: str, amount: float = 1000.0):
    obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix=suffix, amount=amount)
    admin_cookies = auth_admin_cookie(admin)

    r = client.post(
        "/api/finance/payments",
        json={"guestId": guest.id, "amount": amount, "currency": "INR", "idempotencyKey": f"cb-{suffix}"},
        cookies=admin_cookies,
    )
    assert r.status_code == 201, r.text
    payment_id = r.json()["id"]
    r = client.post(
        f"/api/finance/payments/{payment_id}/confirm",
        json={"allocations": [{"obligationId": obligation.id, "amount": amount}]},
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text
    return obligation, payment_id, admin, admin_cookies, party_id


class TestOpenChargebackDispute:
    def test_missing_obligation_or_amount_is_rejected(self, client, db_session: Session):
        _obligation, payment_id, _admin, admin_cookies, _party_id = _confirm_a_payment(client, db_session, suffix="cbopen1")

        r = client.post(
            "/api/finance/disputes",
            json={"paymentId": payment_id, "category": "CHARGEBACK", "description": "no amount/obligation"},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

    def test_opening_one_creates_an_open_hold(self, client, db_session: Session):
        obligation, payment_id, _admin, admin_cookies, _party_id = _confirm_a_payment(client, db_session, suffix="cbopen2")

        r = client.post(
            "/api/finance/disputes",
            json={
                "paymentId": payment_id, "obligationId": obligation.id, "amount": 1000.0,
                "category": "CHARGEBACK", "description": "card issuer opened a chargeback",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        dispute_id = r.json()["id"]

        hold = db_session.scalar(
            select(FinancialHold).where(FinancialHold.source_type == "dispute_case", FinancialHold.source_id == str(dispute_id))
        )
        assert hold is not None
        assert hold.reason_code == "CHARGEBACK_OPENED"
        assert hold.severity == "HIGH"
        assert hold.status == "OPEN"

    def test_non_chargeback_dispute_is_unaffected(self, client, db_session: Session):
        # A non-financial category needs none of the new fields and creates no hold.
        _obligation, payment_id, _admin, admin_cookies, _party_id = _confirm_a_payment(client, db_session, suffix="cbopen3")

        r = client.post(
            "/api/finance/disputes",
            json={"paymentId": payment_id, "category": "OTHER", "description": "unrelated complaint"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        dispute_id = r.json()["id"]
        assert db_session.scalars(select(FinancialHold).where(FinancialHold.source_id == str(dispute_id))).all() == []


class TestResolveChargebackDispute:
    def test_missing_outcome_is_rejected(self, client, db_session: Session):
        obligation, payment_id, _admin, admin_cookies, _party_id = _confirm_a_payment(client, db_session, suffix="cbres1")
        r = client.post(
            "/api/finance/disputes",
            json={
                "paymentId": payment_id, "obligationId": obligation.id, "amount": 1000.0,
                "category": "CHARGEBACK", "description": "x",
            },
            cookies=admin_cookies,
        )
        dispute_id = r.json()["id"]

        r = client.post(f"/api/finance/disputes/{dispute_id}/resolve", json={"status": "RESOLVED"}, cookies=admin_cookies)
        assert r.status_code == 400, r.text

    def test_lost_reverses_the_collection_and_closes_the_hold(self, client, db_session: Session):
        obligation, payment_id, _admin, admin_cookies, party_id = _confirm_a_payment(client, db_session, suffix="cbres2")
        r = client.post(
            "/api/finance/disputes",
            json={
                "paymentId": payment_id, "obligationId": obligation.id, "amount": 1000.0,
                "category": "CHARGEBACK", "description": "x",
            },
            cookies=admin_cookies,
        )
        dispute_id = r.json()["id"]

        r = client.post(
            f"/api/finance/disputes/{dispute_id}/resolve",
            json={"status": "RESOLVED", "chargebackOutcome": "LOST"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["chargebackOutcome"] == "LOST"

        entry = db_session.scalar(
            select(LedgerEntry).where(LedgerEntry.source_type == "dispute_case", LedgerEntry.source_id == str(dispute_id))
        )
        assert entry is not None
        assert float(entry.amount) == 1000.0

        hold = db_session.scalar(
            select(FinancialHold).where(FinancialHold.source_type == "dispute_case", FinancialHold.source_id == str(dispute_id))
        )
        assert hold.status == "RESOLVED"

        db_session.refresh(obligation)
        assert obligation.status == "REFUNDED"

    def test_won_does_not_touch_the_ledger_but_still_closes_the_hold(self, client, db_session: Session):
        obligation, payment_id, _admin, admin_cookies, _party_id = _confirm_a_payment(client, db_session, suffix="cbres3")
        r = client.post(
            "/api/finance/disputes",
            json={
                "paymentId": payment_id, "obligationId": obligation.id, "amount": 1000.0,
                "category": "CHARGEBACK", "description": "x",
            },
            cookies=admin_cookies,
        )
        dispute_id = r.json()["id"]

        r = client.post(
            f"/api/finance/disputes/{dispute_id}/resolve",
            json={"status": "REJECTED", "chargebackOutcome": "WON"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["chargebackOutcome"] == "WON"

        assert db_session.scalars(
            select(LedgerEntry).where(LedgerEntry.source_type == "dispute_case", LedgerEntry.source_id == str(dispute_id))
        ).all() == []

        hold = db_session.scalar(
            select(FinancialHold).where(FinancialHold.source_type == "dispute_case", FinancialHold.source_id == str(dispute_id))
        )
        assert hold.status == "RESOLVED"

        db_session.refresh(obligation)
        assert obligation.status == "PAID"

    def test_lost_against_an_already_paid_out_obligation_flags_negative_balance(self, client, db_session: Session):
        obligation, payment_id, admin, admin_cookies, party_id = _confirm_a_payment(client, db_session, suffix="cbres4")

        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PAID"

        r = client.post(
            "/api/finance/disputes",
            json={
                "paymentId": payment_id, "obligationId": obligation.id, "amount": 1000.0,
                "category": "CHARGEBACK", "description": "x",
            },
            cookies=admin_cookies,
        )
        dispute_id = r.json()["id"]
        r = client.post(
            f"/api/finance/disputes/{dispute_id}/resolve",
            json={"status": "RESOLVED", "chargebackOutcome": "LOST"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        hold = db_session.scalar(select(FinancialHold).where(FinancialHold.reason_code == "NEGATIVE_ACCOUNT_BALANCE"))
        assert hold is not None
        assert hold.severity == "HIGH"
        assert hold.status == "OPEN"
