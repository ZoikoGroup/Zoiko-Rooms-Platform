from fastapi import APIRouter, Depends, HTTPException, Request, status
from datetime import datetime, timezone

from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_super_admin
from app.core.correlation import get_correlation_id
from app.core.identity_uploads import resolve_identity_document_path
from app.crud import break_glass_access as break_glass_crud
from app.crud import identity_verification as crud
from app.crud.audit import log_audit_event
from app.crud.eligibility import check_offer_eligibility
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.schemas.identity import (
    IdentityErasureRequest,
    IdentityGateUpdate,
    IdentityPackUpdate,
    IdentityReasonMappingUpsert,
)
from app.schemas.marketplace import (
    BreakGlassAccessRequest,
    IdentityVerificationRead,
)

router = APIRouter(prefix="/api/identity-verifications", tags=["identity-verifications"], dependencies=[Depends(get_current_admin)])


@router.get("", response_model=list[IdentityVerificationRead])
def get_identity_verifications(
    party_id: int | None = None,
    status: str | None = None,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return crud.list_identity_verifications(db, admin, party_id, status)


# ZR-IDV-ADR-001: identities are decided only by the identity provider
# (Veriff). There are no admin create / approve / reject / request-evidence
# routes -- admins can view cases, reconcile with the provider, erase data
# and manage the country packs and provider configuration.


@router.get("/metrics", dependencies=[Depends(require_super_admin)])
def get_identity_metrics(days: int = 30, db: Session = Depends(get_db)):
    """ZR-IDENTITY-001 Section 15 -- aggregates only."""
    from app.services.identity import service as identity_service

    return _camel(identity_service.metrics(db, days=max(1, min(days, 365))))


def _camel(value):
    """Same camelCase keys as every CamelModel response. Free-form maps
    keyed by data (method / reason codes) keep their keys."""
    from pydantic.alias_generators import to_camel

    if isinstance(value, dict):
        return {
            to_camel(k): (v if k in ("by_method", "reason_codes") else _camel(v)) for k, v in value.items()
        }
    return value


@router.get("/{verification_id}/case")
def get_identity_case(verification_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    """ZR-IDENTITY-001 Section 13 reviewer case: header, normalized checks
    and the immutable decision history. No full number, no raw scores."""
    from sqlalchemy import select

    from app.models.domain_event import DomainEvent
    from app.models.user_account import UserAccount
    from app.services.identity import service as identity_service

    record = crud.get_identity_verification(db, verification_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identity verification not found")
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    user = db.scalar(select(UserAccount).where(UserAccount.party_id == record.party_id))
    profile = identity_service.get_profile(db, record.party_id)
    events = db.scalars(
        select(DomainEvent)
        .where(DomainEvent.resource_type == "identity_verification", DomainEvent.resource_id == str(record.id))
        .order_by(DomainEvent.occurred_at, DomainEvent.id)
    )
    checks = {k: v for k, v in (record.match_results or {}).items() if k != "duplicate_of_verification_id"}
    return {
        "id": record.id,
        "partyId": record.party_id,
        "accountName": user.full_name if user else "",
        "accountEmail": user.email if user else "",
        "countryCode": record.country_code,
        "roleContext": record.role_context,
        "method": record.method_type,
        "providerCode": record.provider_code,
        "state": record.session_state,
        "assuranceLevel": record.assurance_level,
        "reasonCodes": list(record.reason_codes or []),
        "checks": checks,
        "duplicateOfVerificationId": (record.match_results or {}).get("duplicate_of_verification_id"),
        "legalName": record.legal_name_snapshot,
        "profileState": profile.state if profile else "",
        "documentType": record.document_type,
        "maskedDocumentNumber": record.masked_document_number,
        "providerDecision": record.provider_decision,
        "providerSessionRef": (record.provider_session_id[:8] + "…") if record.provider_session_id else "",
        "consentNoticeVersion": record.consent_notice_version,
        "hasDocument": record.has_document,
        "documentContentType": record.document_file_content_type,
        "evidencePurgedAt": record.evidence_purged_at,
        "verifierNotes": record.verifier_notes,
        "submittedAt": record.submitted_at,
        "decidedAt": record.decided_at,
        "history": [
            {
                "eventType": e.event_type, "occurredAt": e.occurred_at, "actorKind": e.actor_kind,
                "actorId": e.actor_id, "previousState": e.previous_state, "newState": e.new_state,
                "reasonCodes": (e.payload or {}).get("reasonCodes") or ([e.payload["reasonCode"]] if (e.payload or {}).get("reasonCode") else []),
                "decision": (e.payload or {}).get("decision"),
            }
            for e in events
        ],
    }


@router.post("/{verification_id}/reconcile", dependencies=[Depends(require_super_admin)])
def post_identity_reconcile(verification_id: int, request: Request, admin: AdminUser = Depends(require_super_admin),
                            db: Session = Depends(get_db)):
    """ZR-IDV-ADR-001 Section 10: ask the provider for this session's decision."""
    from app.services.identity import service as identity_service

    record = crud.get_identity_verification(db, verification_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identity verification not found")
    outcome = identity_service.reconcile(db, record, correlation_id=get_correlation_id(request))
    log_audit_event(db, admin, "identity_verification.reconcile", "identity_verification", str(verification_id),
                    get_correlation_id(request), reason=outcome)
    db.commit()
    return {"result": outcome}


# ZR-IDV-ADR-001 Section 10: the internal reconciliation action at the path
# the ADR names. Not customer-facing: super admin only.
internal_router = APIRouter(prefix="/internal/identity-verifications", tags=["identity-verifications"],
                            dependencies=[Depends(require_super_admin)])
internal_router.add_api_route("/{verification_id}/reconcile", post_identity_reconcile, methods=["POST"])


@router.post("/parties/{party_id}/erase", dependencies=[Depends(require_super_admin)])
def post_identity_erase(party_id: int, payload: IdentityErasureRequest, request: Request,
                        admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db)):
    """ZR-IDV-ADR-001 Section 12: a lawful deletion request, across Zoiko's
    records and the provider."""
    from app.services.identity import service as identity_service

    result = identity_service.erase_identity_data(
        db, admin, party_id, reason=payload.reason, correlation_id=get_correlation_id(request),
    )
    log_audit_event(db, admin, "identity_verification.erase", "party", str(party_id), get_correlation_id(request),
                    reason=payload.reason[:500])
    db.commit()
    return _camel(result)


@router.get("/packs", dependencies=[Depends(require_super_admin)])
def get_identity_packs(db: Session = Depends(get_db)):
    from app.services.identity import policy

    return [_camel({"id": p.id, "version": p.version, "evidence_retention_days": p.evidence_retention_days,
                    "reverification_interval_days": p.reverification_interval_days,
                    "reverify_on_account_recovery": p.reverify_on_account_recovery,
                    "max_attempts_per_day": p.max_attempts_per_day,
                    "document_provider_code": p.document_provider_code, **policy.pack_summary(p, db)})
            for p in policy.list_packs(db)]


@router.put("/packs/{pack_id}", dependencies=[Depends(require_super_admin)])
def put_identity_pack(pack_id: int, payload: IdentityPackUpdate, request: Request,
                      admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Edits a Country Regulatory Pack by writing a new version (the old one is
    kept, so recorded consent versions stay traceable)."""
    from app.models.identity_profile import IdentityRegulatoryPack
    from app.services.identity import policy

    pack = db.get(IdentityRegulatoryPack, pack_id)
    if pack is None or not pack.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pack not found")
    changes = payload.model_dump(exclude_unset=True)
    try:
        new = policy.new_pack_version(db, pack, changes)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    log_audit_event(db, admin, "identity_pack.update", "identity_regulatory_pack", str(new.id),
                    get_correlation_id(request), reason=",".join(sorted(changes)))
    db.commit()
    return _camel({"id": new.id, "version": new.version, **policy.pack_summary(new, db)})


@router.get("/providers/veriff/readiness", dependencies=[Depends(require_super_admin)])
def get_veriff_readiness(db: Session = Depends(get_db)):
    """ZR-IDV-ADR-001 Sections 15 / 17: automated checks + go-live gates."""
    from app.services.identity import golive

    result = golive.readiness(db)
    db.commit()
    return _camel(result)


@router.put("/providers/veriff/gates/{gate_code}", dependencies=[Depends(require_super_admin)])
def put_veriff_gate(gate_code: str, payload: IdentityGateUpdate, request: Request,
                    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db)):
    from app.services.identity import golive

    gate = golive.set_gate(db, admin, gate_code, confirmed=payload.confirmed, note=payload.note,
                           evidence_reference=payload.evidence_reference)
    log_audit_event(db, admin, f"identity_go_live_gate.{'confirm' if gate.confirmed else 'reopen'}",
                    "identity_go_live_gate", gate_code, get_correlation_id(request), reason=payload.evidence_reference[:200])
    db.commit()
    return _camel(golive.readiness(db))


@router.post("/providers/veriff/connection-test", dependencies=[Depends(require_super_admin)])
def post_veriff_connection_test(request: Request, admin: AdminUser = Depends(require_super_admin),
                                db: Session = Depends(get_db)):
    from app.services.identity import golive

    check = golive.run_connection_test(db, admin)
    log_audit_event(db, admin, "identity_provider.connection_test", "identity_provider", "veriff",
                    get_correlation_id(request), reason="ok" if check.ok else check.detail[:200])
    db.commit()
    return _camel(golive.readiness(db))


@router.get("/providers/reason-mappings", dependencies=[Depends(require_super_admin)])
def get_reason_mappings(db: Session = Depends(get_db)):
    from app.services.identity import golive

    rows = golive.list_reason_mappings(db)
    db.commit()
    return [_camel({"id": r.id, "provider_code": r.provider_code, "provider_decision": r.provider_decision,
                    "provider_reason_code": r.provider_reason_code, "zoiko_reason_code": r.zoiko_reason_code,
                    "description": r.description}) for r in rows]


@router.put("/providers/reason-mappings", dependencies=[Depends(require_super_admin)])
def put_reason_mapping(payload: IdentityReasonMappingUpsert, request: Request,
                       admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db)):
    from app.services.identity import golive

    row = golive.upsert_reason_mapping(
        db, provider_code=payload.provider_code, provider_decision=payload.provider_decision,
        provider_reason_code=payload.provider_reason_code, zoiko_reason_code=payload.zoiko_reason_code,
        description=payload.description,
    )
    log_audit_event(db, admin, "identity_reason_mapping.upsert", "identity_provider_reason_mapping", str(row.id),
                    get_correlation_id(request), reason=f"{row.provider_reason_code}->{row.zoiko_reason_code}")
    db.commit()
    return _camel({"id": row.id, "provider_code": row.provider_code, "provider_decision": row.provider_decision,
                   "provider_reason_code": row.provider_reason_code, "zoiko_reason_code": row.zoiko_reason_code,
                   "description": row.description})


@router.get("/{verification_id}/document")
def download_identity_document(verification_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Super admins can always view uploaded identity documents. A plain
    admin needs an active break-glass grant for this specific record --
    ZR-ENG-CLR-012 AC-27, AC-26 ('support agents cannot browse raw identity
    documents by default')."""
    record = crud.get_identity_verification(db, verification_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identity verification not found")
    if admin.role != "super_admin" and not break_glass_crud.has_valid_break_glass_access(
        db, admin.id, "identity_verification", str(verification_id)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access or an active break-glass grant is required")
    if not record.document_file_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No document was uploaded for this verification")

    path = resolve_identity_document_path(record.document_file_path)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The stored document could not be found")

    # ZR-IDENTITY-001 Section 13: a secure viewer, not a download -- shown
    # inline, never cached, images watermarked with the reviewer and time,
    # and every view audit-logged (Section 9.2 access logging).
    log_audit_event(db, admin, "identity_verification.evidence_viewed", "identity_verification", str(verification_id), "")
    db.commit()
    headers = {"Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff",
               "Content-Disposition": "inline"}
    media_type = record.document_file_content_type or "application/octet-stream"
    watermarked = _watermark_image(path, media_type, f"{admin.email} · {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    if watermarked is not None:
        return Response(content=watermarked, media_type="image/png", headers=headers)
    return FileResponse(path, media_type=media_type, headers=headers)


def _watermark_image(path, media_type: str, label: str) -> bytes | None:
    """Tiles the reviewer/time label over an image document. PDFs are
    watermarked by the reviewer screen's overlay instead."""
    if not media_type.startswith("image/"):
        return None
    try:
        import io

        from PIL import Image, ImageDraw

        image = Image.open(path).convert("RGBA")
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        step = max(120, image.size[1] // 6)
        for y in range(0, image.size[1], step):
            for x in range(-image.size[0], image.size[0], max(260, len(label) * 7)):
                draw.text((x + (y // step) * 40, y), label, fill=(220, 38, 38, 90))
        out = io.BytesIO()
        Image.alpha_composite(image, layer).convert("RGB").save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return None


@router.post("/{verification_id}/break-glass-access", status_code=status.HTTP_201_CREATED)
def post_grant_break_glass_access(
    verification_id: int, payload: BreakGlassAccessRequest, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-012 AC-27: self-service, time-limited, reason-coded,
    audited emergency access for a plain admin to one specific document.
    Grants access to the calling admin only, for BREAK_GLASS_DEFAULT_
    DURATION_MINUTES -- not a permanent role change."""
    record = crud.get_identity_verification(db, verification_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identity verification not found")
    grant = break_glass_crud.grant_break_glass_access(
        db, admin, related_entity_type="identity_verification", related_entity_id=str(verification_id),
        reason=payload.reason,
    )
    log_audit_event(
        db, admin, "identity_verification.break_glass_access", "identity_verification", str(verification_id),
        get_correlation_id(request), reason=payload.reason,
    )
    db.commit()
    return {"grantId": grant.id, "expiresAt": grant.expires_at.isoformat()}
