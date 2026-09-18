"""ZR-ENG-CLR-005 Section 6.4 ('Admin / Finance Operations -- Payment
Console' Actions panel: 'Retry permitted payout'), Section 16.1
(POST /payouts/{id}/retry) and QA-23 ('Payout fails -> Retry/repair payout;
renter remains paid if collection remains valid'). Before this,
crud/finance.py:run_payout's (party_id, period_key) unique constraint
permanently consumed a period the moment it was first called -- a HELD or
FAILED payout had no way back to PAID even after whatever blocked it (an
unverified beneficiary, a missing authority record, ...) was fixed; the only
option was a brand-new payout for a different period, leaving the original
obligations stuck. retry_payout re-evaluates the SAME row in place instead
of creating a second one."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import payout_beneficiary as beneficiary_crud
from app.models.finance import LedgerEntry, PayoutBeneficiary, PayoutRecord
from app.models.party import Party
from app.schemas.finance import PayoutBeneficiarySubmit
from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


class TestRetryPayout:
    def test_retry_succeeds_once_the_beneficiary_gate_is_fixed(self, client, db_session: Session):
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="retry1", amount=1000.0)
        # Remove the auto-verified beneficiary the test helper creates, so the
        # first run holds exactly like TestRunPayoutRequiresAVerifiedBeneficiary does.
        db_session.query(PayoutBeneficiary).filter_by(party_id=party_id).delete()
        db_session.commit()
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "retry1-pay"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]
        client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 1000.0}]},
            cookies=admin_cookies,
        )

        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        payout_id = r.json()["id"]
        assert r.json()["status"] == "HELD"

        # The underlying obligation was never consumed by the HELD run.
        db_session.refresh(obligation)
        assert obligation.status == "PAID"
        assert obligation.payout_id is None

        # Retrying right now must still fail -- nothing has changed yet.
        r = client.post(f"/api/finance/payouts/{payout_id}/retry", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "HELD"

        # Fix the gate: submit and verify a beneficiary for this party. The
        # one-time code is only ever emailed, never returned by the submit
        # API -- same direct-crud-call side channel test_payout_beneficiary.py
        # itself uses to obtain it for testing.
        party = db_session.get(Party, party_id)
        beneficiary, plaintext_code = beneficiary_crud.submit_payout_beneficiary(
            db_session, party, admin,
            PayoutBeneficiarySubmit(
                party_id=party_id, account_holder_name="Retry Landlord", bank_name="Retry Bank",
                account_number="000111222333", bank_identifier_code="TEST0123456",
            ),
        )
        r = client.post(
            f"/api/finance/payout-beneficiaries/{beneficiary.id}/confirm", json={"code": plaintext_code},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(f"/api/finance/payouts/{payout_id}/retry", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "PAID"
        assert body["id"] == payout_id  # same row, never a second payout for this period

        db_session.refresh(obligation)
        assert obligation.payout_id == payout_id

        entries = db_session.scalars(
            select(LedgerEntry).where(LedgerEntry.source_type == "payout_record", LedgerEntry.source_id == str(payout_id))
        ).all()
        assert len(entries) >= 1

        # A second retry on an already-PAID payout is rejected.
        r = client.post(f"/api/finance/payouts/{payout_id}/retry", cookies=admin_cookies)
        assert r.status_code == 409, r.text

    def test_retry_on_a_nonexistent_payout_is_404(self, client, db_session: Session):
        admin = _make_admin(db_session, email="retry-404@test.com", role="super_admin")
        r = client.post("/api/finance/payouts/999999/retry", cookies=auth_admin_cookie(admin))
        assert r.status_code == 404, r.text

    def test_retry_does_not_create_a_second_payout_row_for_the_period(self, client, db_session: Session):
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="retry2", amount=500.0)
        db_session.query(PayoutBeneficiary).filter_by(party_id=party_id).delete()
        db_session.commit()
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "retry2-pay"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]
        client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_cookies,
        )
        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
        )
        payout_id = r.json()["id"]

        for _ in range(3):
            client.post(f"/api/finance/payouts/{payout_id}/retry", cookies=admin_cookies)

        rows = db_session.scalars(select(PayoutRecord).where(PayoutRecord.party_id == party_id)).all()
        assert len(rows) == 1
