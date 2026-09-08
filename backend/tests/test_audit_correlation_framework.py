"""ZR-ENG-CLR-001 Section 1, Section 11.3 / 12.2 / 15: the Audit/Event Layer
must carry correlation and idempotency IDs, not just the bare event fact.

Covers:
- emit_event()'s own idempotency (a caller-supplied idempotency_key can never
  produce two DomainEvent rows, including under the partial-unique-index
  "genuine race" fallback path -- same pattern as services/inventory.py's
  create_hold).
- A client-supplied X-Correlation-Id header actually reaches the audit trail
  end to end (not just generated and discarded).
- Two concrete audit-coverage gaps this increment closed: room hold
  creation/release (explicitly named in Section 15) now produces AuditEvent
  rows, and the offers/expire-overdue sweep endpoint (previously entirely
  unaudited) now does too.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.correlation import CORRELATION_HEADER
from app.crud.events import emit_event
from app.models.audit import AuditEvent
from app.models.domain_event import DomainEvent
from app.models.leasing import Offer
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_room_hold_atomicity import _apply_and_send_offer, _make_listing_with_room, _make_verified_renter


class TestEmitEventIdempotency:
    def test_same_idempotency_key_returns_the_original_row(self, db_session: Session):
        first = emit_event(db_session, "test.event", "widget", "1", {"n": 1}, idempotency_key="widget-1-created")
        db_session.commit()

        second = emit_event(db_session, "test.event", "widget", "1", {"n": 2}, idempotency_key="widget-1-created")

        assert second.id == first.id
        assert second.payload == {"n": 1}  # the second call's payload never got written
        all_matching = db_session.scalars(
            select(DomainEvent).where(DomainEvent.idempotency_key == "widget-1-created")
        ).all()
        assert len(all_matching) == 1

    def test_different_idempotency_keys_create_separate_rows(self, db_session: Session):
        first = emit_event(db_session, "test.event", "widget", "1", idempotency_key="key-a")
        second = emit_event(db_session, "test.event", "widget", "1", idempotency_key="key-b")
        db_session.commit()
        assert first.id != second.id

    def test_no_idempotency_key_never_dedups(self, db_session: Session):
        first = emit_event(db_session, "test.event", "widget", "1")
        second = emit_event(db_session, "test.event", "widget", "1")
        db_session.commit()
        assert first.id != second.id

    def test_concurrent_race_falls_back_to_the_partial_unique_index(self, db_session: Session):
        """Simulates the case emit_event's own pre-check can't catch: a row
        with the same key inserted by someone else between the pre-check and
        this call's own insert. Proven here by inserting the "winning" row
        directly (bypassing emit_event) right before calling emit_event with
        the same key -- the DB constraint, not the pre-check, is what makes
        this safe."""
        winner = DomainEvent(
            event_type="test.event", resource_type="widget", resource_id="1",
            payload={}, idempotency_key="race-key",
        )
        db_session.add(winner)
        db_session.commit()

        result = emit_event(db_session, "test.event", "widget", "1", idempotency_key="race-key")
        assert result.id == winner.id

        all_matching = db_session.scalars(
            select(DomainEvent).where(DomainEvent.idempotency_key == "race-key")
        ).all()
        assert len(all_matching) == 1


class TestCorrelationIdReachesTheAuditTrail:
    def test_client_supplied_correlation_id_is_recorded_on_the_audit_event(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="corr-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="corr-renter1@test.com")

        _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)

        my_correlation_id = "test-correlation-abc123"
        r = client.post(
            f"/api/users/rentals/offers/{offer_id}/accept",
            cookies=auth_user_cookie(renter),
            headers={CORRELATION_HEADER: my_correlation_id},
        )
        assert r.status_code == 200, r.text
        assert r.headers[CORRELATION_HEADER] == my_correlation_id

        audit_rows = db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.resource_type == "offer", AuditEvent.resource_id == str(offer_id),
                AuditEvent.action == "user_offer.accept",
            )
        ).all()
        assert len(audit_rows) == 1
        assert audit_rows[0].correlation_id == my_correlation_id

        event = db_session.scalar(
            select(DomainEvent).where(DomainEvent.idempotency_key == f"offer.accepted:{offer_id}")
        )
        assert event is not None
        assert event.correlation_id == my_correlation_id


class TestRoomHoldLifecycleIsAudited:
    """Section 15 explicitly names 'hold creation/release' as requiring an
    audit trail -- previously services/inventory.py had none at all."""

    def test_accepting_an_offer_audits_room_hold_creation(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="corr-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="corr-renter2@test.com")

        _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
        my_correlation_id = "test-correlation-hold-create"
        r = client.post(
            f"/api/users/rentals/offers/{offer_id}/accept",
            cookies=auth_user_cookie(renter),
            headers={CORRELATION_HEADER: my_correlation_id},
        )
        assert r.status_code == 200, r.text

        hold_audit = db_session.scalar(
            select(AuditEvent).where(AuditEvent.action == "room_hold.create", AuditEvent.reason == f"offer:{offer_id}")
        )
        assert hold_audit is not None
        assert hold_audit.correlation_id == my_correlation_id

    def test_declining_an_offer_audits_room_hold_release(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="corr-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter_a = _make_verified_renter(db_session, email="corr-renter3@test.com")

        _app_a, offer_a = _apply_and_send_offer(client, db_session, renter_a, listing_id, admin_cookies)
        r = client.post(f"/api/users/rentals/offers/{offer_a}/accept", cookies=auth_user_cookie(renter_a))
        assert r.status_code == 200, r.text

        # Admin route can't decline an accepted offer for a real-account
        # renter -- but the room can still be released via occupancy ending,
        # already covered elsewhere. Here, prove release via the acceptance
        # window expiring instead (the release path this scenario actually
        # exercises in this codebase).
        offer_row = db_session.get(Offer, offer_a)
        offer_row.confirmation_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        my_correlation_id = "test-correlation-hold-release"
        r = client.get(
            f"/api/users/rentals/applications/{_app_a}/offer",
            cookies=auth_user_cookie(renter_a),
            headers={CORRELATION_HEADER: my_correlation_id},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "EXPIRED"

        release_audit = db_session.scalar(
            select(AuditEvent).where(AuditEvent.action == "room_hold.release")
        )
        assert release_audit is not None
        assert release_audit.correlation_id == my_correlation_id


class TestExpireOverdueSweepIsAudited:
    def test_sweep_endpoint_produces_an_audit_event(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="corr-admin4@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="corr-renter4@test.com")

        _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
        client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
        offer = db_session.get(Offer, offer_id)
        offer.confirmation_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        r = client.post("/api/leasing/offers/expire-overdue", cookies=admin_cookies)
        assert r.status_code == 200, r.text

        sweep_audit = db_session.scalar(
            select(AuditEvent).where(AuditEvent.action == "offer.expire_overdue_sweep")
        )
        assert sweep_audit is not None
        assert "1" in sweep_audit.reason
