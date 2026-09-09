"""Policy Decision Point (PDP) for chatbot tool authorization (ZR-AI-AUTH-001).

Hybrid RBAC + ABAC/ReBAC, evaluated deterministically outside the model.

* RBAC -- role gating (who may ever exercise the capability family), mirroring
  the previous inline checks in ``execute_tool`` so this is backward compatible.
* ABAC/ReBAC -- attribute- and relationship-based guards for tools that carry a
  resource identity (e.g. listing ownership, PUBLISHED state). These re-check
  the current authoritative relationship for every call, satisfying the
  AUTH-I-005 / AUTH-I-007 object/function re-check invariants instead of
  trusting the model or client-supplied fields.

The PEP (``chat_service.execute_tool``) calls ``check_permission`` before any
handler runs. If it doesn't PERMIT, the tool call is denied with a stable,
non-sensitive reason code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin_user import AdminUser
from app.models.listing import Listing
from app.models.user_account import UserAccount

POLICY_VERSION = "abac-v1"


class Decision(str, Enum):
    PERMIT = "PERMIT"
    DENY = "DENY"
    CHALLENGE = "CHALLENGE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class AuthorizationDecision:
    result: Decision
    reason_code: str = ""
    policy_version: str = POLICY_VERSION


def permit() -> AuthorizationDecision:
    return AuthorizationDecision(Decision.PERMIT)


def deny(reason_code: str) -> AuthorizationDecision:
    return AuthorizationDecision(Decision.DENY, reason_code)


def is_actor(actor: Any) -> str:
    """Return a stable actor kind for RBAC: 'user' or an admin role label."""
    if isinstance(actor, UserAccount):
        return "user"
    if isinstance(actor, AdminUser):
        return actor.role or "admin"
    return "admin"


# ---------------------------------------------------------------------------
# ABAC/ReBAC guards. Each receives the actor plus the raw tool arguments.
# ---------------------------------------------------------------------------


def _resolve_listing(db: Session, args: dict) -> Listing | None:
    listing_id = args.get("listing_id") or args.get("listingId")
    if not listing_id:
        return None
    return db.scalar(select(Listing).where(Listing.id == str(listing_id)))


def _guard_listing_detail(actor: Any, args: dict, db: Session) -> AuthorizationDecision:
    """Ownership / relationship guard for reading a listing's full record.

    Rule:
      * super_admin -> permit
      * non-super admin -> permit only if they own the listing (owner_id)
      * otherwise -> deny
    This re-checks the authoritative relationship every call (AUTH-I-005) so a
    role that permits the tool generally still cannot read another admin's
    private (non-published) listing.
    """
    listing = _resolve_listing(db, args)
    if listing is None:
        return deny("AUTH_RESOURCE_NOT_FOUND")
    if isinstance(actor, AdminUser) and actor.role == "super_admin":
        return permit()
    owner = getattr(listing, "owner_id", None)
    actor_id = getattr(actor, "id", None)
    if owner is not None and owner == actor_id:
        return permit()
    return deny("AUTH_OBJECT_RELATIONSHIP_MISSING")


def _guard_listing_published(actor: Any, args: dict, db: Session) -> AuthorizationDecision:
    """State-based guard for the public listing read (renters/hosts).

    Rule: the listing must exist and be PUBLISHED. Non-published listings are
    never returned to ordinary users regardless of role possession.
    """
    listing = _resolve_listing(db, args)
    if listing is None:
        return deny("AUTH_RESOURCE_NOT_FOUND")
    if listing.state == "PUBLISHED":
        return permit()
    return deny("AUTH_PROPERTY_DENIED")


# Map a declarative tool permission name to its guard. A tool without a guard
# falls through the RBAC layer only.
PERMISSION_GUARDS: dict[str, Any] = {
    "listing.detail": _guard_listing_detail,
    "listing.read_published": _guard_listing_published,
}


def check_permission(actor: Any, tool: Any, args: dict, db: Session) -> AuthorizationDecision:
    """Return a deterministic PERMIT/DENY for ``actor`` invoking ``tool``.

    ``tool`` is expected to expose ``roles``, ``super_admin_only``, ``permission``
    and ``name`` (conforming to the registry in chat_service; typed loosely here
    to avoid a circular import).
    """
    actor_kind = is_actor(actor)

    # --- RBAC -----------------------------------------------------------------
    if actor_kind not in tool.roles:
        return deny("AUTH_SCOPE_MISMATCH")
    if getattr(tool, "super_admin_only", False) and actor_kind != "super_admin":
        return deny("AUTH_SCOPE_MISMATCH")

    # --- ABAC / ReBAC ---------------------------------------------------------
    permission = getattr(tool, "permission", None)
    if permission and permission in PERMISSION_GUARDS:
        return PERMISSION_GUARDS[permission](actor, args, db)

    return permit()