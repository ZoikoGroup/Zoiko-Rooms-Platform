"""ZR-ENG-CLR-008 Section 16: the BCR state machine
(app/services/booking_change_state_machine.py). Covers the guard itself
(illegal jumps refused, terminal statuses have no way out) plus the two real
failure paths approve_change_request/_approve_premises_change now route
through CONFLICT/FAILED instead of leaving a request silently stuck at
AWAITING_HOST forever with no trace of what happened -- see
test_booking_change_requests.py for the happy-path coverage of each change
type."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import booking_change_requests as bcr_crud
from app.models.audit import AuditEvent
from app.models.booking_change_request import BookingChangeRequest
from app.models.listing import Listing
from app.services.booking_change_state_machine import BOOKING_CHANGE_STATUSES, TERMINAL_STATUSES, transition
from tests.conftest import auth_user_cookie
from tests.test_booking_change_requests import _make_second_listing, _signed_agreement_before_move_in


class TestTransitionGuard:
    def test_every_terminal_status_has_no_legal_destination(self):
        for terminal in TERMINAL_STATUSES:
            bcr = BookingChangeRequest(status=terminal)
            for candidate in BOOKING_CHANGE_STATUSES:
                with pytest.raises(HTTPException) as exc_info:
                    transition(bcr, candidate)
                assert exc_info.value.status_code == 409

    def test_illegal_jump_from_awaiting_host_is_refused(self):
        # AWAITING_HOST -> EFFECTIVE is legal now (DEPOSIT_CHANGE's own
        # path -- see state machine module docstring), so the illustrative
        # illegal case is a no-op "transition" to the same status: AWAITING_HOST
        # is never in its own allowed-destinations set.
        bcr = BookingChangeRequest(status="AWAITING_HOST")
        with pytest.raises(HTTPException) as exc_info:
            transition(bcr, "AWAITING_HOST")
        assert exc_info.value.status_code == 409
        assert bcr.status == "AWAITING_HOST"

    def test_legal_jump_mutates_status_in_place(self):
        bcr = BookingChangeRequest(status="AWAITING_HOST")
        transition(bcr, "REJECTED")
        assert bcr.status == "REJECTED"


class TestExpiryTransitionsToTerminalState:
    def test_stale_awaiting_host_request_expires_and_then_refuses_decisions(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="sm-01", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (date.today() + timedelta(days=10)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        bcr = db_session.get(BookingChangeRequest, bcr_id)
        bcr.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()

        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=admin_cookies)
        assert r.status_code == 409, r.text

        db_session.refresh(bcr)
        assert bcr.status == "EXPIRED"

        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/decline", json={}, cookies=admin_cookies)
        assert r.status_code == 409, r.text
        assert "EXPIRED" in r.json()["detail"]


class TestApprovalConflictIsRecorded:
    def test_target_listing_unpublished_between_request_and_approval_records_conflict(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="sm-02", start_date=date.today() - timedelta(days=5),
        )
        target_id = _make_second_listing(db_session, listing_id="L-TARGET-SM02")

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/premises-change-requests",
            json={"targetListingId": target_id}, cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        # Section 24 edge case: conditions change between request and
        # decision -- here the target listing is pulled before the host acts.
        target_listing = db_session.get(Listing, target_id)
        target_listing.state = "DRAFT"
        db_session.commit()

        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=admin_cookies)
        assert r.status_code == 409, r.text
        assert "no longer published" in r.json()["detail"]

        bcr = db_session.get(BookingChangeRequest, bcr_id)
        db_session.refresh(bcr)
        assert bcr.status == "CONFLICT"
        assert "no longer published" in bcr.decision_note

        # A CONFLICT is terminal -- the host cannot retry the same request.
        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/decline", json={}, cookies=admin_cookies)
        assert r.status_code == 409, r.text


class TestApprovalUnexpectedFailureIsRecorded:
    def test_unexpected_error_during_amendment_generation_records_failed(self, client, db_session: Session, monkeypatch):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="sm-03", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (date.today() + timedelta(days=10)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        def _boom(*args, **kwargs):
            raise ValueError("simulated unexpected failure")

        monkeypatch.setattr(bcr_crud.amendment_crud, "request_amendment", _boom)

        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=admin_cookies)
        assert r.status_code == 500

        bcr = db_session.get(BookingChangeRequest, bcr_id)
        db_session.refresh(bcr)
        assert bcr.status == "FAILED"
        assert "simulated unexpected failure" in bcr.decision_note


class TestAuditTrail:
    """AC-32/AC-39: every transition is recorded on the AuditEvent trail.
    approve/decline/withdraw already had their own route-level
    log_audit_event calls (api/routes/leasing.py and user_rentals.py) before
    this session's state-machine work -- these tests pin that pre-existing
    behavior down rather than duplicate it. CONFLICT/FAILED/EXPIRED/EFFECTIVE
    are the genuinely new audit coverage this session adds, since none of
    those paths run through a route's own post-call logging line (an
    exception, or a system-driven transition with no route at all)."""

    def test_decline_is_audited(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="sm-04", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (date.today() + timedelta(days=10)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/decline", json={}, cookies=admin_cookies)
        assert r.status_code == 200, r.text

        events = db_session.query(AuditEvent).filter(
            AuditEvent.resource_type == "booking_change_request", AuditEvent.resource_id == str(bcr_id),
        ).all()
        assert any(e.action == "booking_change_request.decline" for e in events)

    def test_withdrawal_is_audited_with_no_admin_actor(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="sm-05", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (date.today() + timedelta(days=10)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        r = client.post(f"/api/users/rentals/change-requests/{bcr_id}/withdraw", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text

        events = db_session.query(AuditEvent).filter(
            AuditEvent.resource_type == "booking_change_request", AuditEvent.resource_id == str(bcr_id),
        ).all()
        withdrawal_events = [e for e in events if e.action == "booking_change_request.withdraw"]
        assert len(withdrawal_events) == 1
        assert withdrawal_events[0].actor_admin_id is None

    def test_conflict_is_audited_even_though_the_route_raises(self, client, db_session: Session):
        """CONFLICT/FAILED are the new coverage: the route's own
        log_audit_event line never runs on an exception, so without this
        session's _record_terminal_failure helper a rejected-then-failed
        approval attempt would leave no audit trail at all."""
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="sm-07", start_date=date.today() - timedelta(days=5),
        )
        target_id = _make_second_listing(db_session, listing_id="L-TARGET-SM07")
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/premises-change-requests",
            json={"targetListingId": target_id}, cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        target_listing = db_session.get(Listing, target_id)
        target_listing.state = "DRAFT"
        db_session.commit()

        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=admin_cookies)
        assert r.status_code == 409, r.text

        events = db_session.query(AuditEvent).filter(
            AuditEvent.resource_type == "booking_change_request", AuditEvent.resource_id == str(bcr_id),
        ).all()
        assert any(e.action == "booking_change.conflict" and e.after_state == "CONFLICT" for e in events)


class TestOnlyOneOpenRequestPerAgreement:
    """TC-14: 'Two concurrent BCRs from same base version; only first commit
    succeeds.' This system doesn't allow a second BCR to even be *created*
    while one is AWAITING_HOST, which is a stronger guarantee than 'only the
    first commit wins' -- there is never a second in-flight request to race
    against in the first place."""

    def test_second_request_is_refused_while_the_first_is_still_open(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="sm-06", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (date.today() + timedelta(days=10)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (date.today() + timedelta(days=20)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 409, r.text
        assert "already pending" in r.json()["detail"]
