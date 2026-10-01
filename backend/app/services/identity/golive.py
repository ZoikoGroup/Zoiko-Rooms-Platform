"""ZR-IDV-ADR-001 Sections 2, 15 and 17 in the product:

- provider reason-code mappings live in the database (contract-dependent);
- a production provider integration stays off until every go-live gate is
  confirmed by a named admin (fail closed);
- a readiness view combines automated checks (credentials, environment,
  webhook plan, connection test, signed webhooks seen) with those gates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.admin_user import AdminUser
from app.models.identity_profile import IdentityProviderEvent
from app.models.identity_provider_config import (
    GO_LIVE_GATES, IdentityGoLiveGate, IdentityProviderCheck, IdentityProviderReasonMapping,
)
from app.services.identity import providers

PRODUCTION = "production"
WEBHOOK_PLANS = ("decision", "full_auto")

# Starting data written into an empty table only -- maintained in the
# database afterwards (confirm against the contracted Veriff product).
_SEED_REASON_MAPPINGS: tuple[tuple[str, str, str, str, str], ...] = (
    ("veriff", "resubmission_requested", "201", "DOCUMENT_UNREADABLE", "Photos or video missing"),
    ("veriff", "resubmission_requested", "202", "BINDING_FAILED", "Face not clearly visible"),
    ("veriff", "resubmission_requested", "203", "DOCUMENT_UNREADABLE", "Full document not visible"),
    ("veriff", "resubmission_requested", "204", "DOCUMENT_UNREADABLE", "Poor image quality"),
    ("veriff", "resubmission_requested", "205", "DOCUMENT_UNREADABLE", "Document damaged"),
    ("veriff", "resubmission_requested", "206", "DOCUMENT_UNSUPPORTED", "Document type not supported"),
    ("veriff", "resubmission_requested", "207", "DOCUMENT_EXPIRED", "Document expired"),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# -- reason mappings ----------------------------------------------------------------

def ensure_default_reason_mappings(db: Session) -> None:
    existing = {
        (m.provider_code, m.provider_decision, m.provider_reason_code)
        for m in db.scalars(select(IdentityProviderReasonMapping))
    }
    added = False
    for provider_code, decision, code, zoiko, description in _SEED_REASON_MAPPINGS:
        if (provider_code, decision, code) not in existing:
            db.add(IdentityProviderReasonMapping(
                provider_code=provider_code, provider_decision=decision, provider_reason_code=code,
                zoiko_reason_code=zoiko, description=description,
            ))
            added = True
    if added:
        db.flush()


def map_reason(db: Session, provider_code: str, decision: str, reason_code: str) -> str | None:
    if not reason_code:
        return None
    ensure_default_reason_mappings(db)
    row = db.scalar(select(IdentityProviderReasonMapping).where(
        IdentityProviderReasonMapping.provider_code == provider_code,
        IdentityProviderReasonMapping.provider_decision == decision,
        IdentityProviderReasonMapping.provider_reason_code == str(reason_code),
    ))
    return row.zoiko_reason_code if row else None


def list_reason_mappings(db: Session) -> list[IdentityProviderReasonMapping]:
    ensure_default_reason_mappings(db)
    return list(db.scalars(select(IdentityProviderReasonMapping).order_by(
        IdentityProviderReasonMapping.provider_code, IdentityProviderReasonMapping.provider_decision,
        IdentityProviderReasonMapping.provider_reason_code,
    )))


def upsert_reason_mapping(db: Session, *, provider_code: str, provider_decision: str, provider_reason_code: str,
                          zoiko_reason_code: str, description: str = "") -> IdentityProviderReasonMapping:
    from app.services.identity.reason_codes import REASON_CODES

    if zoiko_reason_code not in REASON_CODES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown Zoiko reason code")
    if provider_decision not in ("resubmission_requested", "declined", "review", "expired", "abandoned"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown provider decision")
    row = db.scalar(select(IdentityProviderReasonMapping).where(
        IdentityProviderReasonMapping.provider_code == provider_code,
        IdentityProviderReasonMapping.provider_decision == provider_decision,
        IdentityProviderReasonMapping.provider_reason_code == str(provider_reason_code),
    ))
    if row is None:
        row = IdentityProviderReasonMapping(
            provider_code=provider_code, provider_decision=provider_decision,
            provider_reason_code=str(provider_reason_code), zoiko_reason_code=zoiko_reason_code,
        )
        db.add(row)
    row.zoiko_reason_code = zoiko_reason_code
    row.description = description[:200]
    row.updated_at = _now()
    db.flush()
    return row


# -- go-live gates ---------------------------------------------------------------------

def ensure_gates(db: Session, provider_code: str = "veriff") -> list[IdentityGoLiveGate]:
    existing = {g.gate_code: g for g in db.scalars(select(IdentityGoLiveGate).where(
        IdentityGoLiveGate.provider_code == provider_code))}
    for code, _label in GO_LIVE_GATES:
        if code not in existing:
            existing[code] = IdentityGoLiveGate(provider_code=provider_code, gate_code=code)
            db.add(existing[code])
    db.flush()
    order = [c for c, _ in GO_LIVE_GATES]
    return sorted(existing.values(), key=lambda g: order.index(g.gate_code) if g.gate_code in order else 99)


def set_gate(db: Session, admin: AdminUser, gate_code: str, *, confirmed: bool, note: str = "",
             evidence_reference: str = "", provider_code: str = "veriff") -> IdentityGoLiveGate:
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    if gate_code not in dict(GO_LIVE_GATES):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown go-live gate")
    if confirmed and not (note.strip() or evidence_reference.strip()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Record where the evidence is (a reference or a note)")
    gate = next(g for g in ensure_gates(db, provider_code) if g.gate_code == gate_code)
    gate.confirmed = confirmed
    gate.confirmed_by_admin_id = admin.id if confirmed else None
    gate.confirmed_at = _now() if confirmed else None
    gate.note = note.strip()[:1000]
    gate.evidence_reference = evidence_reference.strip()[:500]
    gate.updated_at = _now()
    db.flush()
    return gate


def provider_enabled(db: Session, provider_code: str) -> tuple[bool, str]:
    """Whether new sessions may be created with this provider right now."""
    if provider_code != providers.VeriffProvider.code:
        return providers.get_provider(provider_code) is not None, ""
    if not providers.veriff_configured():
        return False, "Veriff credentials are not configured"
    if settings.veriff_plan not in WEBHOOK_PLANS:
        return False, f"VERIFF_PLAN must be one of {WEBHOOK_PLANS}"
    if settings.veriff_integration_id == PRODUCTION:
        open_gates = [g.gate_code for g in ensure_gates(db) if not g.confirmed]
        if open_gates:
            return False, f"Production go-live gates open: {', '.join(open_gates)}"
    return True, ""


def resolve_provider(db: Session, provider_code: str) -> providers.IdentityProvider | None:
    enabled, _reason = provider_enabled(db, provider_code)
    return providers.get_provider(provider_code) if enabled else None


# -- connection test / readiness -----------------------------------------------------------

def run_connection_test(db: Session, admin: AdminUser) -> IdentityProviderCheck:
    """Creates (then deletes) a throwaway Veriff session with an opaque test
    reference to prove the credentials and API path work."""
    import uuid

    provider = providers.get_provider(providers.VeriffProvider.code)
    if provider is None:
        check = IdentityProviderCheck(provider_code="veriff", integration_id=settings.veriff_integration_id,
                                      ok=False, detail="Veriff credentials are not configured", run_by_admin_id=admin.id)
    else:
        try:
            created = provider.create_session(vendor_data="zr-connection-test", end_user_id=str(uuid.uuid4()), callback_url="")
            deleted = provider.delete_session(created.provider_session_id)
            check = IdentityProviderCheck(
                provider_code="veriff", integration_id=settings.veriff_integration_id, ok=True,
                detail="Session created" + (" and deleted" if deleted else " (cleanup not confirmed)"),
                run_by_admin_id=admin.id,
            )
        except providers.ProviderUnavailable:
            check = IdentityProviderCheck(provider_code="veriff", integration_id=settings.veriff_integration_id,
                                          ok=False, detail="Veriff refused or could not be reached", run_by_admin_id=admin.id)
    db.add(check)
    db.flush()
    return check


def readiness(db: Session) -> dict:
    gates = ensure_gates(db)
    last_check = db.scalar(select(IdentityProviderCheck).where(IdentityProviderCheck.provider_code == "veriff")
                           .order_by(IdentityProviderCheck.created_at.desc()))
    last_ok = db.scalar(select(IdentityProviderEvent).where(
        IdentityProviderEvent.provider_code == "veriff", IdentityProviderEvent.status != "rejected",
    ).order_by(IdentityProviderEvent.received_at.desc()))
    last_rejected = db.scalar(select(IdentityProviderEvent).where(
        IdentityProviderEvent.provider_code == "veriff", IdentityProviderEvent.status == "rejected",
    ).order_by(IdentityProviderEvent.received_at.desc()))
    enabled, reason = provider_enabled(db, "veriff")
    base = settings.public_api_url.rstrip("/") or "https://<api-host>"
    recent_webhook = bool(last_ok and _as_utc(last_ok.received_at) >= _now() - timedelta(days=7))
    checks = [
        {"code": "CREDENTIALS", "label": "API key and shared secret set in the secret store", "ok": providers.veriff_configured()},
        {"code": "PLAN", "label": f"Webhook contract configured ({settings.veriff_plan})", "ok": settings.veriff_plan in WEBHOOK_PLANS},
        {"code": "CONNECTION_TEST", "label": "Latest connection test passed", "ok": bool(last_check and last_check.ok)},
        {"code": "SIGNED_WEBHOOK", "label": "Authenticated webhook received in the last 7 days", "ok": recent_webhook},
    ]
    return {
        "provider": "veriff",
        "integration": settings.veriff_integration_id,
        "is_production": settings.veriff_integration_id == PRODUCTION,
        "plan": settings.veriff_plan,
        "enabled": enabled,
        "disabled_reason": reason,
        "webhook_urls": {
            "decision": f"{base}/api/v1/webhooks/veriff/decision",
            "events": f"{base}/api/v1/webhooks/veriff/events",
            "full_auto": f"{base}/api/v1/webhooks/veriff/full-auto",
        },
        "checks": checks,
        "last_connection_test": {
            "ok": last_check.ok, "detail": last_check.detail, "at": last_check.created_at,
            "integration": last_check.integration_id,
        } if last_check else None,
        "last_authenticated_webhook_at": last_ok.received_at if last_ok else None,
        "last_rejected_webhook_at": last_rejected.received_at if last_rejected else None,
        "gates": [
            {"code": g.gate_code, "label": dict(GO_LIVE_GATES)[g.gate_code], "confirmed": g.confirmed,
             "confirmed_at": g.confirmed_at, "confirmed_by_admin_id": g.confirmed_by_admin_id,
             "evidence_reference": g.evidence_reference, "note": g.note}
            for g in gates
        ],
    }
