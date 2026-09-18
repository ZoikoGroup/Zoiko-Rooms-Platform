"""ZR-ENG-CLR-008 Section 9/ADMIN_CORRECTION: 'Typo, formatting, metadata
correction that does not alter legal/economic meaning -- non-material if
objectively corrective and fully audited.' This never reaches a material
field (dates, term, rent, target listing, status) -- only this BCR's own
reason/decision_note free-text fields -- and requires an evidence reference,
per Section 9's own table."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.booking_change_request import BookingChangeRequest
from tests.conftest import auth_user_cookie
from tests.test_booking_change_requests import _signed_agreement_before_move_in


def _create_bcr_with_reason(client, agreement_id: int, renter, reason: str) -> int:
    r = client.post(
        f"/api/users/rentals/agreements/{agreement_id}/change-requests",
        json={"proposedStartDate": (date.today() + timedelta(days=10)).isoformat(), "reason": reason},
        cookies=auth_user_cookie(renter),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestAdminCorrection:
    def test_super_admin_can_correct_a_typo_in_the_reason_text(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="corr-01", start_date=date.today() - timedelta(days=5),
        )
        bcr_id = _create_bcr_with_reason(client, agreement_id, renter, "relocatign for work")

        r = client.post(
            f"/api/leasing/booking-change-requests/{bcr_id}/admin-correction",
            json={"correctedReason": "relocating for work", "evidenceRef": "renter confirmed via support chat #4821"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["reason"] == "relocating for work"

        bcr = db_session.get(BookingChangeRequest, bcr_id)
        db_session.refresh(bcr)
        assert bcr.reason == "relocating for work"
        # Correcting metadata never touches the state machine.
        assert bcr.status == "AWAITING_HOST"

    def test_correction_is_audited_with_evidence(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="corr-02", start_date=date.today() - timedelta(days=5),
        )
        bcr_id = _create_bcr_with_reason(client, agreement_id, renter, "typo heree")

        client.post(
            f"/api/leasing/booking-change-requests/{bcr_id}/admin-correction",
            json={"correctedReason": "typo here", "evidenceRef": "screenshot-9911"},
            cookies=admin_cookies,
        )

        events = db_session.query(AuditEvent).filter(
            AuditEvent.resource_type == "booking_change_request", AuditEvent.resource_id == str(bcr_id),
            AuditEvent.action == "booking_change.admin_correction",
        ).all()
        assert len(events) == 1
        assert "screenshot-9911" in events[0].reason
        assert "typo heree" in events[0].reason
        assert "typo here" in events[0].reason

    def test_evidence_reference_is_required(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="corr-03", start_date=date.today() - timedelta(days=5),
        )
        bcr_id = _create_bcr_with_reason(client, agreement_id, renter, "typo heree")

        r = client.post(
            f"/api/leasing/booking-change-requests/{bcr_id}/admin-correction",
            json={"correctedReason": "typo here", "evidenceRef": ""},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

    def test_a_no_op_correction_is_rejected(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="corr-04", start_date=date.today() - timedelta(days=5),
        )
        bcr_id = _create_bcr_with_reason(client, agreement_id, renter, "already correct")

        r = client.post(
            f"/api/leasing/booking-change-requests/{bcr_id}/admin-correction",
            json={"correctedReason": "already correct", "evidenceRef": "n/a"},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

    def test_regular_admin_cannot_correct_only_super_admin_can(self, client, db_session: Session):
        from app.core.security import create_access_token
        from tests.conftest import ADMIN_COOKIE, _make_admin

        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="corr-05", start_date=date.today() - timedelta(days=5),
        )
        bcr_id = _create_bcr_with_reason(client, agreement_id, renter, "typo heree")

        regular_admin = _make_admin(db_session, email="corr-regular-05@test.com", role="admin")
        regular_cookies = {ADMIN_COOKIE: create_access_token(regular_admin.email, token_type="admin")}

        r = client.post(
            f"/api/leasing/booking-change-requests/{bcr_id}/admin-correction",
            json={"correctedReason": "typo here", "evidenceRef": "screenshot-1"},
            cookies=regular_cookies,
        )
        assert r.status_code == 403, r.text

    def test_correction_works_even_on_a_terminal_request(self, client, db_session: Session):
        """A typo fix shouldn't be blocked just because the underlying
        request already reached a terminal state -- it's the historical
        record's display text being fixed, not the decision itself."""
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="corr-06", start_date=date.today() - timedelta(days=5),
        )
        bcr_id = _create_bcr_with_reason(client, agreement_id, renter, "typo heree")
        client.post(f"/api/leasing/booking-change-requests/{bcr_id}/decline", json={}, cookies=admin_cookies)

        r = client.post(
            f"/api/leasing/booking-change-requests/{bcr_id}/admin-correction",
            json={"correctedReason": "typo here", "evidenceRef": "screenshot-2"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "REJECTED"
        assert r.json()["reason"] == "typo here"
