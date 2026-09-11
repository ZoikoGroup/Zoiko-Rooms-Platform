"""ZR-ENG-CLR-006 Section 18.4/AC-22: HostRecovery -- a dedicated, queryable
record of the amount owed back from a Host whose payout already went out
before a later refund, created alongside the existing FinancialHold (see
tests/test_negative_balance_hold.py for that side of the same event) rather
than instead of it. This build automates nothing beyond flagging the amount
-- an admin logs recovery progress or a Super Admin writes it off."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import HostRecovery
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
