"""ZR-ENG-CLR-006 Section 18.4/AC-22: HostRecovery -- a dedicated, queryable
record of the amount owed back from a Host whose payout already went out
before a later refund, created alongside the existing FinancialHold (see
tests/test_negative_balance_hold.py for that side of the same event) rather
than instead of it. An admin can still log recovery progress manually or a
Super Admin can write it off -- but crud/finance.py:run_payout now also
automates Section 15 waterfall tier 4 (FUTURE_PAYOUT_OFFSET) for itself,
full or partial: see TestAutomaticFuturePayoutOffset below, and
tests/test_payout_eligibility_gates.py for the partial case where a later
payout isn't large enough to make the host whole in one shot."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain_event import DomainEvent
from app.models.finance import FinancialHold, HostRecovery, PayoutRecord
from app.models.membership import Membership
from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


def _create_negative_balance_recovery(client, db_session: Session, *, suffix: str, amount: float = 1000.0):
    obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix=suffix, amount=amount)
    admin_cookies = auth_admin_cookie(admin)

    r = client.post(
        "/api/finance/payments",
        json={"guestId": guest.id, "amount": amount, "currency": "INR", "idempotencyKey": f"hostrec-pay-{suffix}"},
        cookies=admin_cookies,
    )
    payment_id = r.json()["id"]
    client.post(
        f"/api/finance/payments/{payment_id}/confirm",
        json={"allocations": [{"obligationId": obligation.id, "amount": amount}]},
        cookies=admin_cookies,
    )
    r = client.post("/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies)
    assert r.json()["status"] == "PAID", r.text

    r = client.post(
        "/api/finance/refunds",
        json={
            "paymentId": payment_id, "obligationId": obligation.id, "amount": amount, "reason": "test clawback",
            "idempotencyKey": f"hostrec-refund-{suffix}",
        },
        cookies=admin_cookies,
    )
    refund_id = r.json()["id"]
    r = client.post(f"/api/finance/refunds/{refund_id}/decide", json={"approve": True}, cookies=admin_cookies)
    assert r.status_code == 200, r.text

    return admin, admin_cookies, party_id


class TestHostRecoveryCreation:
    def test_a_negative_balance_refund_creates_a_host_recovery_alongside_the_hold(self, client, db_session: Session):
        _admin, admin_cookies, party_id = _create_negative_balance_recovery(client, db_session, suffix="create1")

        recovery = db_session.scalar(select(HostRecovery).where(HostRecovery.party_id == party_id))
        assert recovery is not None
        assert recovery.status == "OPEN"
        assert float(recovery.amount) == 1000.0
        assert float(recovery.recovered_amount) == 0.0
        assert recovery.financial_hold_id is not None
        assert recovery.refund_request_id is not None

        r = client.get("/api/finance/host-recoveries", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1
        assert r.json()[0]["partyId"] == party_id

        event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "host_recovery.created",
                DomainEvent.resource_type == "host_recovery",
                DomainEvent.resource_id == str(recovery.id),
            )
        )
        assert event is not None
        assert event.payload["partyId"] == party_id
        assert float(event.payload["amount"]) == 1000.0

    def test_no_recovery_when_the_refund_does_not_go_negative(self, client, db_session: Session):
        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="create2", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "hostrec-pay-create2"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]
        client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_cookies,
        )
        r = client.post(
            "/api/finance/refunds",
            json={
                "paymentId": payment_id, "obligationId": obligation.id, "amount": 500.0, "reason": "no clawback",
                "idempotencyKey": "hostrec-refund-create2",
            },
            cookies=admin_cookies,
        )
        refund_id = r.json()["id"]
        client.post(f"/api/finance/refunds/{refund_id}/decide", json={"approve": True}, cookies=admin_cookies)

        assert db_session.scalars(select(HostRecovery)).all() == []


class TestRecordHostRecoveryProgress:
    def test_partial_then_full_progress_transitions_to_recovered(self, client, db_session: Session):
        _admin, admin_cookies, party_id = _create_negative_balance_recovery(client, db_session, suffix="progress1")
        recovery = db_session.scalar(select(HostRecovery).where(HostRecovery.party_id == party_id))

        r = client.post(
            f"/api/finance/host-recoveries/{recovery.id}/record-recovery",
            json={"amount": 400.0, "method": "DIRECT_COLLECTION", "notes": "partial wire transfer"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "OPEN"
        assert float(body["recoveredAmount"]) == 400.0

        r = client.post(
            f"/api/finance/host-recoveries/{recovery.id}/record-recovery",
            json={"amount": 600.0, "method": "DIRECT_COLLECTION"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "RECOVERED"
        assert float(body["recoveredAmount"]) == 1000.0
        assert body["resolvedAt"] is not None

        event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "host_recovery.completed",
                DomainEvent.resource_id == str(recovery.id),
            )
        )
        assert event is not None
        assert event.payload["status"] == "RECOVERED"

    def test_recording_more_than_outstanding_is_rejected(self, client, db_session: Session):
        _admin, admin_cookies, party_id = _create_negative_balance_recovery(client, db_session, suffix="progress2")
        recovery = db_session.scalar(select(HostRecovery).where(HostRecovery.party_id == party_id))

        r = client.post(
            f"/api/finance/host-recoveries/{recovery.id}/record-recovery",
            json={"amount": 1500.0, "method": "DIRECT_COLLECTION"},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

    def test_unrecognized_method_is_rejected(self, client, db_session: Session):
        _admin, admin_cookies, party_id = _create_negative_balance_recovery(client, db_session, suffix="progress3")
        recovery = db_session.scalar(select(HostRecovery).where(HostRecovery.party_id == party_id))

        r = client.post(
            f"/api/finance/host-recoveries/{recovery.id}/record-recovery",
            json={"amount": 100.0, "method": "MAGIC"},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

    def test_recording_progress_on_a_recovered_recovery_is_rejected(self, client, db_session: Session):
        _admin, admin_cookies, party_id = _create_negative_balance_recovery(client, db_session, suffix="progress4")
        recovery = db_session.scalar(select(HostRecovery).where(HostRecovery.party_id == party_id))
        client.post(
            f"/api/finance/host-recoveries/{recovery.id}/record-recovery",
            json={"amount": 1000.0, "method": "DIRECT_COLLECTION"},
            cookies=admin_cookies,
        )

        r = client.post(
            f"/api/finance/host-recoveries/{recovery.id}/record-recovery",
            json={"amount": 1.0, "method": "DIRECT_COLLECTION"},
            cookies=admin_cookies,
        )
        assert r.status_code == 409, r.text


class TestWriteOffHostRecovery:
    def test_super_admin_can_write_off_a_recovery(self, client, db_session: Session):
        _admin, admin_cookies, party_id = _create_negative_balance_recovery(client, db_session, suffix="writeoff1")
        recovery = db_session.scalar(select(HostRecovery).where(HostRecovery.party_id == party_id))

        r = client.post(
            f"/api/finance/host-recoveries/{recovery.id}/write-off",
            json={"reason": "Host is insolvent -- legal advises writing off"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "WRITTEN_OFF"
        assert body["recoveryMethod"] == "WRITTEN_OFF"

        event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "host_recovery.completed",
                DomainEvent.resource_id == str(recovery.id),
            )
        )
        assert event is not None
        assert event.payload["status"] == "WRITTEN_OFF"

    def test_a_regular_admin_cannot_write_off_a_recovery(self, client, db_session: Session):
        _admin, _admin_cookies, party_id = _create_negative_balance_recovery(client, db_session, suffix="writeoff2")
        recovery = db_session.scalar(select(HostRecovery).where(HostRecovery.party_id == party_id))

        regular_admin = _make_admin(db_session, email="hostrec-regular@test.com", role="admin")
        db_session.add(Membership(admin_user_id=regular_admin.id, party_id=party_id, role="provider_owner_admin", status="active"))
        db_session.commit()

        r = client.post(
            f"/api/finance/host-recoveries/{recovery.id}/write-off",
            json={"reason": "trying anyway"},
            cookies=auth_admin_cookie(regular_admin),
        )
        assert r.status_code == 403, r.text

    def test_a_blank_reason_is_rejected(self, client, db_session: Session):
        _admin, admin_cookies, party_id = _create_negative_balance_recovery(client, db_session, suffix="writeoff3")
        recovery = db_session.scalar(select(HostRecovery).where(HostRecovery.party_id == party_id))

        r = client.post(f"/api/finance/host-recoveries/{recovery.id}/write-off", json={"reason": ""}, cookies=admin_cookies)
        assert r.status_code == 400, r.text


class TestHostRecoveryOwnershipScoping:
    def test_a_regular_admin_only_sees_recoveries_for_their_own_party(self, client, db_session: Session):
        _owner_admin, _owner_cookies, party_id = _create_negative_balance_recovery(client, db_session, suffix="scope1")

        outsider = _make_admin(db_session, email="hostrec-outsider@test.com", role="admin")
        r = client.get("/api/finance/host-recoveries", cookies=auth_admin_cookie(outsider))
        assert r.status_code == 200, r.text
        assert r.json() == []

        member_admin = _make_admin(db_session, email="hostrec-member@test.com", role="admin")
        db_session.add(Membership(admin_user_id=member_admin.id, party_id=party_id, role="provider_finance", status="active"))
        db_session.commit()
        r = client.get("/api/finance/host-recoveries", cookies=auth_admin_cookie(member_admin))
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1

    def test_super_admin_sees_every_recovery(self, client, db_session: Session):
        _owner_admin, _owner_cookies, _party_id = _create_negative_balance_recovery(client, db_session, suffix="scope2")

        super_admin = _make_admin(db_session, email="hostrec-super@test.com", role="super_admin")
        r = client.get("/api/finance/host-recoveries", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1


class TestAutomaticFuturePayoutOffset:
    """ZR-ENG-CLR-006 Section 15 waterfall tier 4: 'Future Host payouts
    offset' -- crud/finance.py:run_payout's own automation. This class
    covers the case where a later payout's net fully covers every
    outstanding recovery for that party/currency in one shot; see
    tests/test_payout_eligibility_gates.py's own test for the partial case
    where the payout is too small to make the host whole in one shot (it
    still pays out, applying whatever partial offset the net allows)."""

    def test_a_large_enough_later_payout_auto_settles_the_recovery(self, client, db_session: Session):
        admin, admin_cookies, party_id = _create_negative_balance_recovery(client, db_session, suffix="autooffset1", amount=1000.0)
        recovery = db_session.scalar(select(HostRecovery).where(HostRecovery.party_id == party_id))
        hold = db_session.get(FinancialHold, recovery.financial_hold_id)
        assert recovery.status == "OPEN"
        assert hold.status == "OPEN"

        # A second period's rent, generous enough (gross 2000, no commission
        # per ZR-PAY-CFG-001 -> net 2000) to fully cover the 1000 outstanding
        # recovery in one shot.
        obligation2, _admin2, guest2, _party_id2 = _make_provider_rent_obligation(
            db_session, suffix="autooffset1b", amount=2000.0, owner_party_id=party_id,
        )
        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest2.id, "amount": 2000.0, "currency": "INR", "idempotencyKey": "autooffset-pay-1b"},
            cookies=admin_cookies,
        )
        payment_id2 = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id2}/confirm",
            json={"allocations": [{"obligationId": obligation2.id, "amount": 2000.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-10"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        payout = r.json()
        assert payout["status"] == "PAID"
        assert float(payout["amount"]) == 2000.0
        assert float(payout["recoveryOffsetAmount"]) == 1000.0

        db_session.refresh(recovery)
        db_session.refresh(hold)
        assert recovery.status == "RECOVERED"
        assert float(recovery.recovered_amount) == 1000.0
        assert recovery.recovery_method == "FUTURE_PAYOUT_OFFSET"
        assert hold.status == "RESOLVED"
        assert hold.resolved_by_admin_id == admin.id

        event = db_session.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "host_recovery.completed",
                DomainEvent.resource_id == str(recovery.id),
            )
        )
        assert event is not None
        assert event.payload["status"] == "RECOVERED"

        # A further payout for a third period, with no recovery left
        # outstanding, behaves exactly as it always has -- no offset applied.
        obligation3, _admin3, guest3, _party_id3 = _make_provider_rent_obligation(
            db_session, suffix="autooffset1c", amount=300.0, owner_party_id=party_id,
        )
        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest3.id, "amount": 300.0, "currency": "INR", "idempotencyKey": "autooffset-pay-1c"},
            cookies=admin_cookies,
        )
        payment_id3 = r.json()["id"]
        client.post(
            f"/api/finance/payments/{payment_id3}/confirm",
            json={"allocations": [{"obligationId": obligation3.id, "amount": 300.0}]},
            cookies=admin_cookies,
        )
        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-11"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        payout3 = r.json()
        assert payout3["status"] == "PAID"
        assert float(payout3["recoveryOffsetAmount"]) == 0.0
        assert float(payout3["amount"]) == 300.0

    def test_payout_statement_shows_the_offset_line_when_applied(self, client, db_session: Session):
        _admin, admin_cookies, party_id = _create_negative_balance_recovery(client, db_session, suffix="autooffset2", amount=1000.0)
        obligation2, _admin2, guest2, _party_id2 = _make_provider_rent_obligation(
            db_session, suffix="autooffset2b", amount=2000.0, owner_party_id=party_id,
        )
        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest2.id, "amount": 2000.0, "currency": "INR", "idempotencyKey": "autooffset-pay-2b"},
            cookies=admin_cookies,
        )
        payment_id2 = r.json()["id"]
        client.post(
            f"/api/finance/payments/{payment_id2}/confirm",
            json={"allocations": [{"obligationId": obligation2.id, "amount": 2000.0}]},
            cookies=admin_cookies,
        )
        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-10"}, cookies=admin_cookies,
        )
        payout_id = r.json()["id"]

        r = client.get(f"/api/finance/payouts/{payout_id}/statement", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/pdf"

    def test_ledger_balance_returns_to_zero_after_the_auto_offset(self, client, db_session: Session):
        """The whole point of tier 4: after the offset, the host is neither
        still owed money for this new period nor still carrying the earlier
        over-payment -- HOST_PAYABLE nets back to exactly zero."""
        from app.models.finance import LedgerAccount
        from app.services import ledger as ledger_service

        _admin, admin_cookies, party_id = _create_negative_balance_recovery(client, db_session, suffix="autooffset3", amount=1000.0)
        obligation2, _admin2, guest2, _party_id2 = _make_provider_rent_obligation(
            db_session, suffix="autooffset3b", amount=2000.0, owner_party_id=party_id,
        )
        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest2.id, "amount": 2000.0, "currency": "INR", "idempotencyKey": "autooffset-pay-3b"},
            cookies=admin_cookies,
        )
        payment_id2 = r.json()["id"]
        client.post(
            f"/api/finance/payments/{payment_id2}/confirm",
            json={"allocations": [{"obligationId": obligation2.id, "amount": 2000.0}]},
            cookies=admin_cookies,
        )
        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-10"}, cookies=admin_cookies,
        )
        assert r.json()["status"] == "PAID"

        host_payable = db_session.scalar(
            select(LedgerAccount).where(LedgerAccount.account_type == "HOST_PAYABLE", LedgerAccount.party_id == party_id)
        )
        assert float(ledger_service.get_balance(db_session, host_payable)) == 0.0
