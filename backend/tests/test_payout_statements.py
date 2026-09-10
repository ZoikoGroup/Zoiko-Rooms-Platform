"""ZR-ENG-CLR-005 Section 6.3/13.1: run_payout generates an immutable
PayoutStatement best-effort (only when the payout is actually PAID, never
for HELD); the download route serves the same rendered PDF, is idempotent,
is provider-ownership-scoped, and 409s for a payout that's on hold."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import PayoutStatement
from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


def _pay_and_run_payout(client, db_session: Session, *, suffix: str, amount: float = 1000.0):
    obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix=suffix, amount=amount)
    admin_cookies = auth_admin_cookie(admin)

    r = client.post(
        "/api/finance/payments",
        json={"guestId": guest.id, "amount": amount, "currency": "INR", "idempotencyKey": f"stmt-{suffix}"},
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

    r = client.post(
        "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"], admin, admin_cookies


class TestRunPayoutGeneratesAStatement:
    def test_paid_payout_creates_exactly_one_statement(self, client, db_session: Session):
        payout_id, _admin, _admin_cookies = _pay_and_run_payout(client, db_session, suffix="stmtgen1")

        statements = db_session.scalars(select(PayoutStatement).where(PayoutStatement.payout_id == payout_id)).all()
        assert len(statements) == 1
        assert statements[0].statement_number == f"STMT-{payout_id:08d}"


class TestPayoutStatementDownload:
    def test_owning_admin_can_download_the_statement_pdf(self, client, db_session: Session):
        payout_id, _admin, admin_cookies = _pay_and_run_payout(client, db_session, suffix="stmtdl1")

        r = client.get(f"/api/finance/payouts/{payout_id}/statement", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/pdf"
        assert r.content.startswith(b"%PDF")
        assert "attachment" in r.headers["content-disposition"]

    def test_calling_it_twice_is_idempotent(self, client, db_session: Session):
        payout_id, _admin, admin_cookies = _pay_and_run_payout(client, db_session, suffix="stmtdl2")

        r1 = client.get(f"/api/finance/payouts/{payout_id}/statement", cookies=admin_cookies)
        r2 = client.get(f"/api/finance/payouts/{payout_id}/statement", cookies=admin_cookies)
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.content == r2.content

        statements = db_session.scalars(select(PayoutStatement).where(PayoutStatement.payout_id == payout_id)).all()
        assert len(statements) == 1

    def test_other_providers_admin_cannot_download_it(self, client, db_session: Session):
        payout_id, _admin, _admin_cookies = _pay_and_run_payout(client, db_session, suffix="stmtdl3")
        outsider = _make_admin(db_session, email="stmt-outsider@test.com", role="admin")

        r = client.get(f"/api/finance/payouts/{payout_id}/statement", cookies=auth_admin_cookie(outsider))
        assert r.status_code == 403, r.text

    def test_no_statement_for_a_held_payout(self, client, db_session: Session):
        # No AuthorityRecord for this room -> run_payout holds it.
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="stmtheld1", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)
        from app.models.authority_record import AuthorityRecord
        db_session.query(AuthorityRecord).filter_by(party_id=party_id).delete()
        db_session.commit()

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "stmt-held-1"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        payout = r.json()
        assert payout["status"] == "HELD"

        r = client.get(f"/api/finance/payouts/{payout['id']}/statement", cookies=admin_cookies)
        assert r.status_code == 409, r.text
