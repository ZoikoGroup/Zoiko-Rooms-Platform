"""ZR-ENG-CLR-012 Section 9/AC-31: "Time-limited eligibility creates a
FOLLOW_UP_REQUIRED date and an automated notification/task before expiry."

No job scheduler exists in this stack (see services/booking_expiry.py's own
docstring on the same limitation). Consistent with that existing pattern,
this is an on-demand bulk sweep for admin/ops use until a real scheduler
exists -- run it the same way check_alerts.py / reconcile_expirations.py are
already run as standalone scripts, or wire it behind an admin action."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.models.identity_verification import IdentityVerification
from app.models.occupancy_eligibility_check import OccupancyEligibilityCheck
from app.models.property_compliance_credential import PropertyComplianceCredential
from app.models.room import Room

# How far ahead of follow_up_due_at to raise the reminder -- not specified as
# a fixed number by the doc (which leaves the exact lead time to jurisdiction
# policy), so this uses the same "reasonable default, not a verified legal
# figure" honesty as MarketPolicyPack's other numeric defaults.
FOLLOW_UP_REMINDER_LEAD_DAYS = 30


def sweep_occupancy_eligibility_follow_ups(db: Session, *, now: datetime | None = None) -> list[OccupancyEligibilityCheck]:
    """Notifies the renter and all admins once per check when its
    follow_up_due_at falls within FOLLOW_UP_REMINDER_LEAD_DAYS (including
    already-overdue ones) -- idempotent via follow_up_notified_at, so a check
    already notified is never re-notified even if the sweep runs daily."""
    now = now or datetime.now(timezone.utc)
    horizon = now + timedelta(days=FOLLOW_UP_REMINDER_LEAD_DAYS)

    candidates = db.scalars(
        select(OccupancyEligibilityCheck).where(
            OccupancyEligibilityCheck.status == "PASS",
            OccupancyEligibilityCheck.follow_up_due_at.is_not(None),
            OccupancyEligibilityCheck.follow_up_due_at <= horizon,
            OccupancyEligibilityCheck.follow_up_notified_at.is_(None),
        )
    ).all()

    notified: list[OccupancyEligibilityCheck] = []
    for check in candidates:
        due_str = check.follow_up_due_at.date().isoformat()
        notif_crud.notify_user_by_party(
            db, check.party_id,
            title="Occupancy eligibility check due for renewal",
            message=f"Your {check.jurisdiction_code} occupancy eligibility check needs to be renewed by {due_str}.",
            notification_type="occupancy_eligibility.follow_up_due",
            related_entity_type="occupancy_eligibility_check", related_entity_id=str(check.id),
        )
        notif_crud.notify_all_super_admins(
            db,
            title="Occupancy eligibility follow-up due",
            message=f"Party #{check.party_id}'s {check.jurisdiction_code} eligibility check is due for renewal by {due_str}.",
            notification_type="occupancy_eligibility.follow_up_due",
            related_entity_type="occupancy_eligibility_check", related_entity_id=str(check.id),
        )
        check.follow_up_notified_at = now
        notified.append(check)

    if notified:
        db.commit()
    return notified


def sweep_identity_verification_follow_ups(db: Session, *, now: datetime | None = None) -> list[IdentityVerification]:
    """AC-31 applied to identity credentials, not just occupancy eligibility
    -- same idempotent, lead-time-window shape as the sweep above."""
    now = now or datetime.now(timezone.utc)
    horizon = now + timedelta(days=FOLLOW_UP_REMINDER_LEAD_DAYS)

    candidates = db.scalars(
        select(IdentityVerification).where(
            IdentityVerification.status == "verified",
            IdentityVerification.expires_at.is_not(None),
            IdentityVerification.expires_at <= horizon,
            IdentityVerification.expiry_notified_at.is_(None),
        )
    ).all()

    notified: list[IdentityVerification] = []
    for record in candidates:
        due_str = record.expires_at.date().isoformat()
        notif_crud.notify_user_by_party(
            db, record.party_id,
            title="Identity verification due for renewal",
            message=f"Your identity verification expires on {due_str}. Submit a current document to stay verified.",
            notification_type="identity_verification.expiry_due",
            related_entity_type="identity_verification", related_entity_id=str(record.id),
        )
        record.expiry_notified_at = now
        notified.append(record)

    if notified:
        db.commit()
    return notified


def sweep_property_compliance_follow_ups(db: Session, *, now: datetime | None = None) -> list[PropertyComplianceCredential]:
    """AC-31 applied to property compliance credentials -- notifies the
    property's own owner (the Host), never the renter, since this
    credential belongs to the property, not a person."""
    now = now or datetime.now(timezone.utc)
    horizon = now + timedelta(days=FOLLOW_UP_REMINDER_LEAD_DAYS)

    candidates = db.scalars(
        select(PropertyComplianceCredential).where(
            PropertyComplianceCredential.status == "VALID",
            PropertyComplianceCredential.expires_at.is_not(None),
            PropertyComplianceCredential.expires_at <= horizon,
            PropertyComplianceCredential.expiry_notified_at.is_(None),
        )
    ).all()

    notified: list[PropertyComplianceCredential] = []
    for credential in candidates:
        room = db.get(Room, credential.room_id)
        owner_party_id = room.property.owner_party_id if room and room.property else None
        due_str = credential.expires_at.date().isoformat()
        notif_crud.notify_user_by_party(
            db, owner_party_id,
            title="Property compliance credential due for renewal",
            message=f"Your {credential.requirement_code} credential expires on {due_str}. Renew it to avoid a publication/booking block.",
            notification_type="property_compliance_credential.expiry_due",
            related_entity_type="property_compliance_credential", related_entity_id=str(credential.id),
        )
        credential.expiry_notified_at = now
        notified.append(credential)

    if notified:
        db.commit()
    return notified
