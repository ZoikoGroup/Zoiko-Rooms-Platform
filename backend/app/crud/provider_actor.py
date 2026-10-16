"""The provider side of a tenancy can be acted on by an admin (the admin portal,
acting for a provider party it has a membership in) or by the self-service
host (a UserAccount acting on their own party's listing). These helpers let
the leasing engines take either, recording each in its own column and audit
form, the same way crud/leasing.py's offer/agreement pipeline already does
via assert_provider_access_any."""

from app.models.admin_user import AdminUser
from app.models.user_account import UserAccount

ProviderActor = AdminUser | UserAccount


def admin_or_none(actor: ProviderActor) -> AdminUser | None:
    """What log_audit_event takes as its actor -- a host user is recorded in
    the audit reason instead (audit_reason below)."""
    return actor if isinstance(actor, AdminUser) else None


def admin_id(actor: ProviderActor) -> int | None:
    return actor.id if isinstance(actor, AdminUser) else None


def user_id(actor: ProviderActor) -> int | None:
    return actor.id if isinstance(actor, UserAccount) else None


def audit_reason(actor: ProviderActor, reason: str = "") -> str:
    if isinstance(actor, UserAccount):
        return f"host_user:{actor.id}" + (f" {reason}" if reason else "")
    return reason


def event_actor(actor: ProviderActor) -> tuple[str, str]:
    """(actor_kind, actor_id) for emit_event."""
    return ("admin", str(actor.id)) if isinstance(actor, AdminUser) else ("host_user", str(actor.id))
