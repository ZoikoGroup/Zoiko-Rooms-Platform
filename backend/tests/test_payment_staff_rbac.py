"""Integration tests for the ZR-PAY-LINK-003 Section 17 Permissions Matrix's
narrow "Staff" tier: app/models/admin_user.py's new payment_staff_role
column and app/api/deps.py:require_super_admin_or_payment_staff, gating
POST /records/{id}/confirm, /records/{id}/provider-confirm and
/records/{id}/corrections in app/api/routes/rental_payments.py.

Covers: a plain admin (no payment_staff_role) is refused (403) on all three,
same as before this field existed; an admin flagged PAYMENT_SUPPORT can use
all three; that same staff admin is still refused (403) on waive/cancel/
reverse, which stay super_admin-only; a super_admin can always use all six
regardless of payment_staff_role."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.crud import rental_payment as rp_crud
from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_rental_payment_records import _make_obligation


def _make_confirmable_record(db: Session):
    obligation, tenant, _recipient = _make_obligation(db)
    record = rp_crud.mark_paid(
        db, tenant, obligation, amount=850, currency="GBP", declared_date=date.today(),
        payment_method_category="BANK_TRANSFER",
    )
    return obligation, record


class TestPlainAdminStillRefused:
    def test_plain_admin_gets_403_on_confirm_provider_confirm_and_corrections(self, client, db_session: Session):
        _obligation, record = _make_confirmable_record(db_session)
        admin = _make_admin(db_session, email="staff-rbac-plain@test.com", role="admin")
        assert admin.payment_staff_role is None
        cookies = auth_admin_cookie(admin)

        r = client.post(f"/api/finance/rental-payments/records/{record.id}/confirm", json={"reason": "test"}, cookies=cookies)
        assert r.status_code == 403, r.text

        r = client.post(
            f"/api/finance/rental-payments/records/{record.id}/provider-confirm",
            json={"providerReference": "ref-1", "reason": "test"}, cookies=cookies,
        )
        assert r.status_code == 403, r.text

        r = client.post(
            f"/api/finance/rental-payments/records/{record.id}/corrections",
            json={"fieldName": "external_reference", "newValue": "NEW-REF", "reason": "test"}, cookies=cookies,
        )
        assert r.status_code == 403, r.text


class TestPaymentStaffCanConfirmAndCorrect:
    def test_payment_support_staff_can_confirm(self, client, db_session: Session):
        _obligation, record = _make_confirmable_record(db_session)
        staff = _make_admin(db_session, email="staff-rbac-confirm@test.com", role="admin")
        staff.payment_staff_role = "PAYMENT_SUPPORT"
        db_session.commit()

        r = client.post(
            f"/api/finance/rental-payments/records/{record.id}/confirm",
            json={"reason": "recipient lost account access"}, cookies=auth_admin_cookie(staff),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CONFIRMED"

    def test_payment_support_staff_can_provider_confirm(self, client, db_session: Session):
        _obligation, record = _make_confirmable_record(db_session)
        staff = _make_admin(db_session, email="staff-rbac-provider@test.com", role="admin")
        staff.payment_staff_role = "PAYMENT_SUPPORT"
        db_session.commit()

        r = client.post(
            f"/api/finance/rental-payments/records/{record.id}/provider-confirm",
            json={"providerReference": "ref-1", "reason": "matched bank reconciliation"}, cookies=auth_admin_cookie(staff),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CONFIRMED"

    def test_payment_support_staff_can_append_a_correction(self, client, db_session: Session):
        _obligation, record = _make_confirmable_record(db_session)
        staff = _make_admin(db_session, email="staff-rbac-correction@test.com", role="admin")
        staff.payment_staff_role = "PAYMENT_SUPPORT"
        db_session.commit()

        r = client.post(
            f"/api/finance/rental-payments/records/{record.id}/corrections",
            json={"fieldName": "external_reference", "newValue": "NEW-REF", "reason": "typo fix"},
            cookies=auth_admin_cookie(staff),
        )
        assert r.status_code == 201, r.text


class TestPaymentStaffCannotDoHigherRiskActions:
    def test_payment_support_staff_gets_403_on_waive_cancel_and_reverse(self, client, db_session: Session):
        obligation, record = _make_confirmable_record(db_session)
        staff = _make_admin(db_session, email="staff-rbac-restricted@test.com", role="admin")
        staff.payment_staff_role = "PAYMENT_SUPPORT"
        db_session.commit()
        cookies = auth_admin_cookie(staff)

        r = client.post(
            f"/api/finance/rental-payments/obligations/{obligation.id}/waive", json={"reason": "test"}, cookies=cookies,
        )
        assert r.status_code == 403, r.text

        r = client.post(
            f"/api/finance/rental-payments/obligations/{obligation.id}/cancel", json={"reason": "test"}, cookies=cookies,
        )
        assert r.status_code == 403, r.text

        r = client.post(
            f"/api/finance/rental-payments/records/{record.id}/reverse", json={"reason": "test"}, cookies=cookies,
        )
        assert r.status_code == 403, r.text


class TestSuperAdminUnaffected:
    def test_super_admin_can_still_confirm_and_reverse_regardless_of_payment_staff_role(self, client, db_session: Session):
        _obligation, record = _make_confirmable_record(db_session)
        super_admin = _make_admin(db_session, email="staff-rbac-super@test.com", role="super_admin")
        assert super_admin.payment_staff_role is None
        cookies = auth_admin_cookie(super_admin)

        r = client.post(
            f"/api/finance/rental-payments/records/{record.id}/confirm", json={"reason": "test"}, cookies=cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/finance/rental-payments/records/{record.id}/reverse", json={"reason": "test"}, cookies=cookies,
        )
        assert r.status_code == 200, r.text


class TestAdminUsersRouteValidatesPaymentStaffRole:
    def test_creating_an_admin_rejects_an_unknown_payment_staff_role(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="staff-rbac-creator@test.com", role="super_admin")
        r = client.post(
            "/api/admin-users",
            json={
                "email": "staff-rbac-created@test.com", "password": "password123", "role": "admin",
                "paymentStaffRole": "NOT_A_REAL_ROLE",
            },
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 400, r.text

    def test_creating_an_admin_accepts_the_declared_payment_staff_role(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="staff-rbac-creator2@test.com", role="super_admin")
        r = client.post(
            "/api/admin-users",
            json={
                "email": "staff-rbac-created2@test.com", "password": "password123", "role": "admin",
                "paymentStaffRole": "PAYMENT_SUPPORT",
            },
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 201, r.text
        assert r.json()["paymentStaffRole"] == "PAYMENT_SUPPORT"
