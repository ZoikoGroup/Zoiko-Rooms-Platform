"""Integration tests for ZR-ENG-CLR-010 Section 23/28's audit_event
previous_hash/new_hash tamper-evident hash chain:
app/crud/audit.py:log_audit_event's new chaining logic.

Covers: the first chained event has previous_hash=None (an honest "chain
starts here" marker, not a fabricated link to unchained history) and a
real new_hash; a second event chains its previous_hash to the first
event's new_hash; tampering with an earlier event's recorded content would
change what its new_hash *should* be, so a later event's previous_hash no
longer matches -- the detection mechanism this chain exists for; every
existing log_audit_event call site (this module is shared platform-wide,
not dispute-specific) still returns a normal AuditEvent."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.audit import _compute_new_hash, log_audit_event
from app.models.audit import AuditEvent
from tests.conftest import _make_admin


class TestHashChainBasics:
    def test_the_first_event_in_an_empty_chain_has_no_previous_hash_but_a_real_new_hash(self, db_session: Session):
        admin = _make_admin(db_session, email="ahc-admin1@test.com", role="super_admin")
        event = log_audit_event(db_session, admin, "test.action", "widget", "1")
        db_session.commit()

        assert event.previous_hash is None
        assert event.new_hash is not None
        assert len(event.new_hash) == 64

    def test_a_second_event_chains_to_the_first_events_new_hash(self, db_session: Session):
        admin = _make_admin(db_session, email="ahc-admin2@test.com", role="super_admin")
        first = log_audit_event(db_session, admin, "test.action", "widget", "1")
        db_session.commit()
        second = log_audit_event(db_session, admin, "test.action", "widget", "2")
        db_session.commit()

        assert second.previous_hash == first.new_hash
        assert second.new_hash != first.new_hash

    def test_the_new_hash_is_reproducible_from_the_events_own_recorded_content(self, db_session: Session):
        admin = _make_admin(db_session, email="ahc-admin3@test.com", role="super_admin")
        event = log_audit_event(db_session, admin, "test.action", "widget", "1", reason="because")
        db_session.commit()

        recomputed = _compute_new_hash(event.previous_hash, event)
        assert recomputed == event.new_hash

    def test_tampering_with_an_earlier_events_content_is_detectable(self, db_session: Session):
        """The whole point of the chain: if someone edits an already-written
        row's content in place, recomputing its hash from that (tampered)
        content no longer matches what it originally recorded, and the
        next row's previous_hash then points at a hash the tampered row
        can no longer reproduce."""
        admin = _make_admin(db_session, email="ahc-admin4@test.com", role="super_admin")
        first = log_audit_event(db_session, admin, "test.action", "widget", "1")
        db_session.commit()
        second = log_audit_event(db_session, admin, "test.action", "widget", "2")
        db_session.commit()

        # Simulate tampering: mutate the first row's content without
        # updating its hash (append-only in real usage -- this is exactly
        # the kind of out-of-band edit the chain is meant to catch).
        first.reason = "tampered"
        db_session.commit()

        recomputed_first_hash = _compute_new_hash(first.previous_hash, first)
        assert recomputed_first_hash != first.new_hash  # the row's own hash no longer matches its (tampered) content
        assert second.previous_hash != recomputed_first_hash  # and the chain link to it is now broken


class TestSharedAcrossAllDomains:
    def test_an_ordinary_call_site_still_gets_a_normal_audit_event(self, db_session: Session):
        """log_audit_event is shared by every domain (finance, leasing,
        listings, etc.) -- confirms adding the hash chain didn't change
        its return type/shape for any existing caller."""
        admin = _make_admin(db_session, email="ahc-admin5@test.com", role="super_admin")
        event = log_audit_event(
            db_session, admin, "listing.publish", "listing", "L-1", "corr-1",
            reason="ok", before_state="DRAFT", after_state="PUBLISHED",
        )
        db_session.commit()

        fetched = db_session.scalar(select(AuditEvent).where(AuditEvent.id == event.id))
        assert fetched.action == "listing.publish"
        assert fetched.before_state == "DRAFT"
        assert fetched.after_state == "PUBLISHED"
        assert fetched.new_hash is not None
