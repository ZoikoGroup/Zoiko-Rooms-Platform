"""ZR-ENG-CLR-012 Section 9/AC-31: "Time-limited eligibility creates a
FOLLOW_UP_REQUIRED date and an automated notification/task before expiry."
Covers the on-demand sweep (no scheduler exists in this stack -- see
services/verification_followups.py's own docstring) and its idempotency."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.identity_verification import verify_identity_verification
from app.crud.occupancy_eligibility import open_occupancy_eligibility_check, record_occupancy_eligibility_result
from app.crud.property_compliance import issue_property_compliance_credential
from app.models.identity_verification import IdentityVerification
from app.models.notification import Notification
from app.models.party import Party
from app.services.verification_followups import (
    sweep_identity_verification_follow_ups,
    sweep_occupancy_eligibility_follow_ups,
    sweep_property_compliance_follow_ups,
)
from tests.conftest import _make_admin


def _passed_check_with_follow_up(db: Session, admin, *, follow_up_in_days: int, email_suffix: str):
    party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()
    db.commit()
    check = open_occupancy_eligibility_check(
        db, admin, party_id=party.id, jurisdiction_code="England", method="MANUAL_DOCUMENT_CHECK",
    )
    record_occupancy_eligibility_result(
        db, check, admin, result_status="PASS", reason_note="Manual document check passed", follow_up_days=follow_up_in_days,
    )
    return check, party


class TestFollowUpSweep:
    def test_check_due_soon_is_notified(self, db_session: Session):
        admin = _make_admin(db_session, email="vfu-admin-01@test.com", role="super_admin")
        check, party = _passed_check_with_follow_up(db_session, admin, follow_up_in_days=10, email_suffix="01")

        notified = sweep_occupancy_eligibility_follow_ups(db_session)
        assert [c.id for c in notified] == [check.id]

        db_session.refresh(check)
        assert check.follow_up_notified_at is not None

    def test_check_far_in_the_future_is_not_notified(self, db_session: Session):
        admin = _make_admin(db_session, email="vfu-admin-02@test.com", role="super_admin")
        check, party = _passed_check_with_follow_up(db_session, admin, follow_up_in_days=365, email_suffix="02")

        notified = sweep_occupancy_eligibility_follow_ups(db_session)
        assert notified == []

        db_session.refresh(check)
        assert check.follow_up_notified_at is None

    def test_sweep_is_idempotent_never_double_notifies(self, db_session: Session):
        admin = _make_admin(db_session, email="vfu-admin-03@test.com", role="super_admin")
        check, party = _passed_check_with_follow_up(db_session, admin, follow_up_in_days=5, email_suffix="03")

        first = sweep_occupancy_eligibility_follow_ups(db_session)
        assert len(first) == 1
        second = sweep_occupancy_eligibility_follow_ups(db_session)
        assert second == []

    def test_already_overdue_check_is_notified(self, db_session: Session):
        admin = _make_admin(db_session, email="vfu-admin-04@test.com", role="super_admin")
        check, party = _passed_check_with_follow_up(db_session, admin, follow_up_in_days=1, email_suffix="04")
        check.follow_up_due_at = datetime.now(timezone.utc) - timedelta(days=2)
        db_session.commit()

        notified = sweep_occupancy_eligibility_follow_ups(db_session)
        assert [c.id for c in notified] == [check.id]


class TestIdentityVerificationFollowUpSweep:
    def test_verified_record_due_soon_is_notified(self, db_session: Session):
        admin = _make_admin(db_session, email="vfu-idv-admin-01@test.com", role="super_admin")
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        record = IdentityVerification(party_id=party.id, document_type="passport", status="pending")
        db_session.add(record)
        db_session.commit()
        verify_identity_verification(db_session, record, admin)
        record.expires_at = datetime.now(timezone.utc) + timedelta(days=10)
        db_session.commit()

        notified = sweep_identity_verification_follow_ups(db_session)
        assert [r.id for r in notified] == [record.id]
        db_session.refresh(record)
        assert record.expiry_notified_at is not None

    def test_sweep_is_idempotent(self, db_session: Session):
        admin = _make_admin(db_session, email="vfu-idv-admin-02@test.com", role="super_admin")
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        record = IdentityVerification(party_id=party.id, document_type="passport", status="pending")
        db_session.add(record)
        db_session.commit()
        verify_identity_verification(db_session, record, admin)
        record.expires_at = datetime.now(timezone.utc) + timedelta(days=10)
        db_session.commit()

        first = sweep_identity_verification_follow_ups(db_session)
        assert len(first) == 1
        second = sweep_identity_verification_follow_ups(db_session)
        assert second == []


class TestPropertyComplianceFollowUpSweep:
    def test_credential_due_soon_notifies_the_property_owner(self, client, db_session: Session):
        from tests.test_room_hold_atomicity import _make_listing_with_room

        listing_id, room_id = _make_listing_with_room(db_session)
        admin = _make_admin(db_session, email="vfu-pcc-admin-01@test.com", role="super_admin")

        credential = issue_property_compliance_credential(
            db_session, admin, room_id=room_id, requirement_code="GAS_SAFETY_CERT",
            expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        )

        notified = sweep_property_compliance_follow_ups(db_session)
        assert [c.id for c in notified] == [credential.id]
        db_session.refresh(credential)
        assert credential.expiry_notified_at is not None

    def test_sweep_is_idempotent(self, client, db_session: Session):
        from tests.test_room_hold_atomicity import _make_listing_with_room

        listing_id, room_id = _make_listing_with_room(db_session)
        admin = _make_admin(db_session, email="vfu-pcc-admin-02@test.com", role="super_admin")

        issue_property_compliance_credential(
            db_session, admin, room_id=room_id, requirement_code="GAS_SAFETY_CERT",
            expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        )

        first = sweep_property_compliance_follow_ups(db_session)
        assert len(first) == 1
        second = sweep_property_compliance_follow_ups(db_session)
        assert second == []
