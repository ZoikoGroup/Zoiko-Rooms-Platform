"""ZR-ENG-CLR-008 Section 4/LEGAL_ORDER_CHANGE: 'Court, regulator or
statutory change requiring amendment | System/Admin initiated; authority
evidence mandatory.' Unlike every other change type, this is admin-created
(no renter request) and skips straight to AWAITING_AGREEMENT_ACTION -- there
is no host decision to wait for once a court/regulator has already decided;
the authority_evidence_ref is the recorded gate Section 6 requires instead
of renter/host consent."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.models.leasing import Agreement
from tests.conftest import ADMIN_COOKIE, _make_admin
from tests.test_booking_change_requests import _signed_agreement_before_move_in


class TestAdminCreateLegalOrderChange:
    def test_super_admin_can_create_a_rent_change_by_legal_order(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="lo-01", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/legal-order-changes",
            json={
                "proposedMonthlyRent": 450, "authorityEvidenceRef": "Rent Tribunal Order RT-2026-0091",
                "reason": "Tribunal-ordered rent reduction",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["changeType"] == "LEGAL_ORDER_CHANGE"
        assert body["status"] == "AWAITING_AGREEMENT_ACTION"
        assert body["proposedMonthlyRent"] == 450
        assert body["authorityEvidenceRef"] == "Rent Tribunal Order RT-2026-0091"
        assert body["resultingAmendmentId"] is not None

        agreement = db_session.get(Agreement, agreement_id)
        db_session.refresh(agreement)
        assert agreement.status == "AMENDMENT_PENDING"
        assert float(agreement.offer.terms[-1].monthly_rent) == 450

    def test_evidence_reference_is_required(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="lo-02", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/legal-order-changes",
            json={"proposedMonthlyRent": 450, "authorityEvidenceRef": ""},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

    def test_at_least_one_proposed_field_is_required(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="lo-03", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/legal-order-changes",
            json={"authorityEvidenceRef": "Order RT-1"},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

    def test_regular_admin_cannot_create_only_super_admin_can(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="lo-04", start_date=date.today() - timedelta(days=5),
        )
        regular_admin = _make_admin(db_session, email="lo-regular-04@test.com", role="admin")
        regular_cookies = {ADMIN_COOKIE: create_access_token(regular_admin.email, token_type="admin")}

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/legal-order-changes",
            json={"proposedMonthlyRent": 450, "authorityEvidenceRef": "Order RT-1"},
            cookies=regular_cookies,
        )
        assert r.status_code == 403, r.text

    def test_can_change_dates_and_term_together_by_legal_order(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="lo-05", start_date=date.today() - timedelta(days=5),
        )
        new_start = date.today() + timedelta(days=30)
        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/legal-order-changes",
            json={
                "proposedStartDate": new_start.isoformat(), "newTermMonths": 12,
                "authorityEvidenceRef": "Council Notice CN-55",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["proposedStartDate"] == new_start.isoformat()
        assert body["additionalTermMonths"] == 12
