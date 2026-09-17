"""ZR-ENG-CLR-012 Section 9/28, AC-28/29/31: reconciliation safety net for the
four verification sweeps (occupancy-eligibility follow-ups, identity-
verification follow-ups, property-compliance follow-ups, evidence-artifact
retention deletion). Unlike the offer/checkout expiry clocks in
reconcile_expirations.py, these four have no lazy self-healing read path --
without this script (or someone manually hitting the equivalent
super_admin-only /api/verification/*/sweep-* routes) they never run at all.

Intended to run on a schedule (e.g. a Render cron job) -- this backend has no
in-process job scheduler, so this is a standalone script in the same spirit
as check_alerts.py and reconcile_expirations.py rather than a background task.

Run with: python reconcile_verification_sweeps.py
"""
from app.crud.audit import log_audit_event
from app.db.session import SessionLocal
from app.services.evidence_retention import sweep_expired_evidence
from app.services.verification_followups import (
    sweep_identity_verification_follow_ups,
    sweep_occupancy_eligibility_follow_ups,
    sweep_property_compliance_follow_ups,
)


def reconcile_verification_sweeps() -> None:
    db = SessionLocal()
    try:
        # No HTTP request/admin session exists here -- system-triggered
        # audit events use actor=None, the same convention already used by
        # crud/booking_change_requests.py:_expire_if_overdue for its own
        # cron-substitute sweep.
        occupancy_notified = sweep_occupancy_eligibility_follow_ups(db)
        if occupancy_notified:
            log_audit_event(
                db, None, "occupancy_eligibility.follow_up_sweep", "occupancy_eligibility_check", "bulk",
                reason=f"notified {len(occupancy_notified)} check(s)",
            )
            db.commit()

        identity_notified = sweep_identity_verification_follow_ups(db)
        if identity_notified:
            log_audit_event(
                db, None, "identity_verification.follow_up_sweep", "identity_verification", "bulk",
                reason=f"notified {len(identity_notified)} record(s)",
            )
            db.commit()

        compliance_notified = sweep_property_compliance_follow_ups(db)
        if compliance_notified:
            log_audit_event(
                db, None, "property_compliance_credential.follow_up_sweep", "property_compliance_credential", "bulk",
                reason=f"notified {len(compliance_notified)} credential(s)",
            )
            db.commit()

        deleted = sweep_expired_evidence(db)
        if deleted:
            log_audit_event(
                db, None, "evidence_artifact.retention_sweep", "evidence_artifact", "bulk",
                reason=f"deleted {len(deleted)} artifact(s)",
            )
            db.commit()

        print(
            f"Notified {len(occupancy_notified)} occupancy-eligibility check(s), "
            f"{len(identity_notified)} identity-verification record(s), "
            f"{len(compliance_notified)} property-compliance credential(s); "
            f"deleted {len(deleted)} expired evidence artifact(s)."
        )
    finally:
        db.close()


if __name__ == "__main__":
    reconcile_verification_sweeps()
