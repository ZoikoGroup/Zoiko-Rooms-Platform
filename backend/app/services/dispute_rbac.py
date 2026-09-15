"""ZR-ENG-CLR-010 Section 12/20: enforcement for the named dispute
specialist roles (Support/Dispute Officer/Finance/Trust & Safety/
Legal-Compliance) modeled on AdminUser.dispute_role (see
models/admin_user.py's own DISPUTE_ADMIN_ROLES comment for why this is a
second, disputes-only field rather than a change to the global
admin/super_admin role every other domain already depends on).

An admin with no dispute_role assigned -- every admin that existed before
this field did, and any admin nobody has specialized yet -- is NOT treated
as having no access: this build has no migration step that back-assigns a
specialization to existing admins, and locking every one of them out of
disputes on deploy would be a silent, un-auditable access regression, not a
security improvement. assert_dispute_role therefore only ever narrows
access for an admin who HAS been given a specific, different
specialization -- it is an opt-in restriction that activates one admin at a
time as they're assigned a role, not a default-deny gate. super_admin
bypasses unconditionally, matching every other admin.role check in this
codebase."""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.crud.events import emit_event
from app.models.admin_user import AdminUser

# QA-Q40/Section 21/25: the two dispute roles Section 25 names as having
# "restricted access to safety/harassment/discrimination/fraud material"
# (TRUST_AND_SAFETY) or a "privileged/restricted workspace" (LEGAL_COMPLIANCE)
# -- the only roles (besides super_admin) ever allowed to read a
# PRIVILEGED_RESTRICTED evidence item. See
# crud/dispute_evidence.py:_admin_may_see_privileged, which -- unlike
# assert_dispute_role below -- defaults to DENY, not allow, for an
# unspecialized admin: Section 25 states "ordinary Support cannot access
# restricted item" as the default rule, not as a narrowing that only
# applies once configured.
PRIVILEGED_EVIDENCE_DISPUTE_ROLES = ("TRUST_AND_SAFETY", "LEGAL_COMPLIANCE")


def assert_dispute_role(admin: AdminUser, *allowed: str, db: Session | None = None) -> None:
    """AC-7: "Super Admin cannot bypass authority controls without an
    authorized, audited legal/compliance override path." A plain
    super_admin (dispute_role is None, the overwhelming common case) isn't
    "bypassing" anything -- there's no narrower role assigned to compare
    against, so nothing here is an override. The one case that IS a real
    override -- a super_admin who has ALSO been assigned a specific
    dispute_role, acting outside that assigned role's own lane -- is only
    reachable by a Super Admin at all (a non-super-admin with the same
    conflicting dispute_role is hard-blocked below), which is this
    system's "authorized" gate; `db` (every caller already has one) turns
    that reachability into an actual audited event rather than a silent
    pass-through, via the same emit_event outbox every other domain event
    already uses (see app/crud/events.py)."""
    if admin.role == "super_admin":
        if admin.dispute_role is not None and admin.dispute_role not in allowed and db is not None:
            emit_event(
                db, "dispute_rbac.override_used", "admin_user", str(admin.id),
                {"adminDisputeRole": admin.dispute_role, "requiredRoles": list(allowed)},
                actor_kind="admin", actor_id=str(admin.id),
            )
        return
    if admin.dispute_role is None:
        return
    if admin.dispute_role not in allowed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"This action requires the {'/'.join(allowed)} dispute role (this admin is {admin.dispute_role})",
        )
