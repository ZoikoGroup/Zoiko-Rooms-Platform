from sqlalchemy.orm import Session

from app.models.admin_user import AdminUser
from app.models.audit import AuditEvent


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
    )
    db.add(event)
    db.flush()
    return event
