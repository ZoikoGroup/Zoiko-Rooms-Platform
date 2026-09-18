import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin_user import AdminUser
from app.models.audit import AuditEvent


def _compute_new_hash(previous_hash: str | None, event: AuditEvent) -> str:
    """ZR-ENG-CLR-010 Section 23/28: chains this event's own content to
    the previous row's hash so the sequence, not just each row in
    isolation, is tamper-evident."""
    canonical = json.dumps(
        {
            "id": event.id,
            "actor_admin_id": event.actor_admin_id,
            "action": event.action,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "reason": event.reason,
            "correlation_id": event.correlation_id,
            "before_state": event.before_state,
            "after_state": event.after_state,
            "created_at": event.created_at.isoformat(),
            "previous_hash": previous_hash,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def log_audit_event(
    db: Session,
    actor: AdminUser | None,
    action: str,
    resource_type: str,
    resource_id: str,
    correlation_id: str = "",
    reason: str = "",
    before_state: str | None = None,
    after_state: str | None = None,
    object_version: str | None = None,
    policy_version: str | None = None,
) -> AuditEvent:
    # Section 23/28's hash chain: the immediately preceding row in the
    # single, global, append-only sequence -- its own new_hash becomes
    # this row's previous_hash. A pre-chain historical row (previous_hash/
    # new_hash both null) still chains cleanly from here on: the first
    # chained row simply has previous_hash=None, an honest "chain starts
    # here" marker rather than a fabricated link to unchained history.
    previous = db.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    previous_hash = previous.new_hash if previous else None

    event = AuditEvent(
        actor_admin_id=actor.id if actor else None,
        role=actor.role if actor else "user",
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        reason=reason,
        correlation_id=correlation_id,
        before_state=before_state,
        after_state=after_state,
        object_version=object_version,
        policy_version=policy_version,
        previous_hash=previous_hash,
    )
    db.add(event)
    db.flush()
    event.new_hash = _compute_new_hash(previous_hash, event)
    db.flush()
    return event
