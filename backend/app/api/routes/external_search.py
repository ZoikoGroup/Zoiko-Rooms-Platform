"""REST surface for the ZR-AI-SEARCH-001 external search protocol.

These need point at the same deterministic services the chat tools use
(``search_orchestrator`` / ``external_outreach``), so the UI and the assistant
agree on the search waterfall, rights gates and masked-card rules.

Auth: user routes are scoped to ``zoiko_user_token``; admin routes to
``zoiko_admin_token``. Source-registry listing additionally requires super admin
(mirroring the ``manage_source_registry`` chat tool).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone as _tz

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, get_current_user, require_super_admin
from app.core.correlation import get_correlation_id
from app.core.rate_limit import external_search_limiter
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.external_search import ExternalOpportunity, ProviderOutreach, SourceRightRegistry
from app.models.user_account import UserAccount
from app.schemas.external_search import (
    ExternalCardResult,
    ExternalContactRestRequest,
    ExternalSearchRestRequest,
    ExternalSearchRestResponse,
    OutreachCreated,
    SourceRegistryUpsert,
    SearchState as SearchStateEnum,
)
from app.services.audit_ext import log_external_search_event
from app.services.external_outreach import outreach_service
from app.services.feed_formats import FEED_FORMATS, FeedFormatError, parse_feed
from app.services.feed_sync import MAX_FEED_BYTES
from app.services.external_broker import validate_fetch_url
from app.services.partner_feeds import partner_feed_adapter
from app.services.source_rights_registry import registry
from app.services.search_orchestrator import SearchQuery, orchestrator, persist_external_cards

router = APIRouter(prefix="/api/users/external-search", tags=["user-external-search"], dependencies=[Depends(get_current_user)])
admin_router = APIRouter(prefix="/api/admin/external-search", tags=["admin-external-search"], dependencies=[Depends(get_current_admin)])

def _rate_limit_or_raise(
    request: Request,
    user_id: int | None = None,
    *,
    resource: str = "search",
) -> None:
    """Section 8 abuse controls: bulk/scraping-style throttling per actor."""
    if not external_search_limiter.allow(f"external_search:{resource}:{user_id or 'anonymous'}"):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Rate limit exceeded for external search. Try again shortly.",
        )


def _consent_record(message: str, consent_fields: list[str]) -> dict:
    """What the renter explicitly agreed to share (Section 7.4), recorded with
    the intro request before any outreach is dispatched."""
    return {
        "basis": "user_requested_contact",
        "message": message,
        "consent_fields": consent_fields,
        "at": datetime.now(_tz.utc).isoformat(),
    }


@router.post("/search", response_model=ExternalSearchRestResponse)
def external_search(
    payload: ExternalSearchRestRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExternalSearchRestResponse:
    """Internal-first search waterfall with controlled external fallback."""
    import logging

    _rate_limit_or_raise(request, user.id, resource="search")
    logger = logging.getLogger("zoiko.external_search")
    correlation_id = get_correlation_id(request)
    query = SearchQuery(
        q=payload.q,
        city=payload.city,
        country=payload.country,
        min_price=payload.min_price,
        max_price=payload.max_price,
        move_in_from=payload.move_in_from,
        move_in_to=payload.move_in_to,
        room_type=payload.room_type,
        objective_filters=payload.objective_filters,
        limit_internal=payload.limit_internal,
        limit_external=payload.limit_external,
    )
    result = orchestrator.search(db, query, correlation_id=correlation_id, actor_id=user.id)
    disc = result.discovery
    logger.info(
        "user external-search q=%r city=%r state=%s internal=%d external=%d",
        payload.q, payload.city, disc.state.value, disc.internal_matches, len(disc.external_matches),
    )

    # Persist each discovered extra listing so the client can start a
    # consent-gated contact request with a real opportunity id. Discovery is a
    # platform record (ZR-AI-SEARCH-001 15.3), not just a rendered card.
    cards_out: list[ExternalCardResult] = []
    if disc.state == SearchStateEnum.EXTERNAL_DISCOVERED:
        cards_out = persist_external_cards(db, disc.external_matches, user.id, result.external_private)

    db.commit()
    return ExternalSearchRestResponse(
        state=disc.state.value,
        internal_matches=disc.internal_matches,
        internal_results=result.internal_results,
        external_matches=cards_out,
        fallback_triggered=disc.fallback_triggered,
        consent_required=disc.consent_required,
        disclosure_text=disc.disclosure_text,
        guardrail_notes=disc.guardrail_notes,
        audit_id=disc.audit_id,
    )


@router.post("/opportunities/{opportunity_id}/contact", response_model=OutreachCreated)
def request_provider_contact(
    opportunity_id: int,
    payload: ExternalContactRestRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OutreachCreated:
    """Record a user's request for Zoiko Rooms to contact an external provider.

    Runs Section 9 step 1 (INTRO_REQUESTED) and step 2 (eligibility, channel
    from the source's rights) via ``request_intro``. Fails closed: unknown,
    BLOCKED or rights-less opportunities are rejected, and only the user whose
    search discovered the opportunity can request it.
    """
    _rate_limit_or_raise(request, user.id, resource="contact")

    po_record: ProviderOutreach
    try:
        po_record = outreach_service.request_intro(
            db,
            user_id=user.id,
            opportunity_id=opportunity_id,
            consent_fields=_consent_record(payload.message, payload.consent_fields),
            correlation_id=get_correlation_id(request),
        )
    except PermissionError as exc:
        log_external_search_event(
            db,
            action="outreach.blocked.rights",
            resource_type="external_opportunity",
            resource_id=str(opportunity_id),
            correlation_id=get_correlation_id(request),
            reason=f"user:{user.id} {exc}",
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc) or "Contact is not allowed for this opportunity")
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc) or "Opportunity not found")
    db.commit()
    return OutreachCreated(
        outreach_id=po_record.id,
        status=po_record.outreach_status,
        channel=po_record.channel,
    )


@router.get("/opportunities")
def my_external_opportunities(
    _request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """List external discovery requests the current user has made (masked).

    Never exposes provider contacts, URLs, phones, emails, or exact addresses.
    """
    rows = db.execute(
        select(ProviderOutreach, ExternalOpportunity)
        .join(ExternalOpportunity, ProviderOutreach.opportunity_id == ExternalOpportunity.id)
        .where(ProviderOutreach.requested_by_user_id == user.id)
        .order_by(ExternalOpportunity.discovered_at.desc())
        .limit(50)
    ).all()
    out = []
    for po, opp in rows:
        out.append(
            {
                "opportunity_id": opp.id,
                "external_opportunity_id": opp.external_opportunity_id,
                "status": opp.status,
                "verification_status": opp.verification_status,
                "approx_location": opp.approx_location,
                "outreach_status": po.outreach_status,
                "provider_response": po.provider_response or "NO_RESPONSE",
                "requested_at": po.requested_at.isoformat() if po.requested_at else None,
                "masked": True,
            }
        )
    return out


@admin_router.get("/metrics", dependencies=[Depends(require_super_admin)])
def external_search_metrics(
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """ZR-AI-SEARCH-001 Section 16 operational metrics (super-admin only)."""
    from app.services.external_search_metrics import external_search_metrics as compute

    days = int(request.query_params.get("days", 30))
    return compute(db, days=days)


@admin_router.get("/outreach")
def external_outreach_queue(
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Pending provider outreach requests across users (admin queue)."""
    limit = min(int(request.query_params.get("limit", 50)) or 50, 50)
    rows = db.execute(
        select(ProviderOutreach, ExternalOpportunity)
        .join(ExternalOpportunity, ProviderOutreach.opportunity_id == ExternalOpportunity.id)
        .where(ProviderOutreach.outreach_status == "PENDING")
        .order_by(ProviderOutreach.requested_at.asc())
        .limit(limit)
    ).all()
    if not rows:
        return []

    user_ids = {po.requested_by_user_id for po, _ in rows}
    users = {
        u.id: u
        for u in db.scalars(select(UserAccount).where(UserAccount.id.in_(user_ids))).all()
    }
    out = []
    for po, opp in rows:
        usr = users.get(po.requested_by_user_id)
        out.append(
            {
                "outreach_id": po.id,
                "opportunity_id": opp.id,
                "external_opportunity_id": opp.external_opportunity_id,
                "status": opp.status,
                "verification_status": opp.verification_status,
                "approx_location": opp.approx_location,
                "channel": po.channel,
                "outreach_status": po.outreach_status,
                "requested_at": po.requested_at.isoformat() if po.requested_at else None,
                "requested_by_user_id": po.requested_by_user_id,
                "requested_by_email": usr.email if usr else None,
            }
        )
    return out


@admin_router.get("/registry", dependencies=[Depends(require_super_admin)])
def source_rights_registry_list(
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Source Rights Registry entries (super-admin only)."""
    stmt = select(SourceRightRegistry).order_by(SourceRightRegistry.source_id)
    source_id = request.query_params.get("source_id")
    if source_id:
        stmt = stmt.where(SourceRightRegistry.source_id == source_id)
    rules = db.scalars(stmt.limit(100)).all()
    out = []
    for r in rules:
        out.append(
            {
                "source_id": r.source_id,
                "source_name_internal": r.source_name_internal,
                "status": r.status,
                "acquisition_mode": r.acquisition_mode,
                "territories": r.territories,
                "legal_approved": r.legal_approved,
                "security_approved": r.security_approved,
                "display_permitted": r.display_permitted,
                "masking_permitted": r.masking_permitted,
                "contact_extraction_permitted": r.contact_extraction_permitted,
                "outreach_permitted": r.outreach_permitted,
                "outreach_channels": r.outreach_channels,
                "terms_reference": r.terms_reference,
            }
        )
    return out


@admin_router.post("/feeds/{source_id}/upload", dependencies=[Depends(require_super_admin)])
async def upload_partner_feed(
    source_id: str,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    """Apply an uploaded partner feed file (e.g. a UK agent's BLM export) as
    that partner's current inventory. Same rules as the scheduled pull: the
    source must be an ACTIVE, approved PARTNER_FEED. ``?format=`` overrides
    the registry's feed_format (JSON | CSV | BLM | RESO)."""
    src = db.scalar(select(SourceRightRegistry).where(SourceRightRegistry.source_id == source_id))
    if src is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    fmt = (request.query_params.get("format") or src.feed_format or "").upper()
    if fmt not in FEED_FORMATS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Set the feed format to one of {', '.join(FEED_FORMATS)}",
        )
    content = await file.read(MAX_FEED_BYTES + 1)
    if len(content) > MAX_FEED_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Feed file is larger than 20 MB")
    try:
        items = parse_feed(content, fmt)
        result = partner_feed_adapter.sync_snapshot(
            db, source_id=source_id, items=items, correlation_id=get_correlation_id(request)
        )
    except FeedFormatError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Couldn't read the feed: {exc}")
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    db.commit()
    return {"source_id": source_id, "format": fmt, "listings": len(items), **result}


@admin_router.put("/registry/{source_id}", dependencies=[Depends(require_super_admin)])
def upsert_source_rights(
    source_id: str,
    payload: SourceRegistryUpsert,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Create or update a Source Rights Registry row (super admin only).

    A source is used only when status is ACTIVE and both approvals are set;
    the change is audited and takes effect immediately."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,99}", source_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "source_id must be lowercase letters, digits, '_', '-' or '.'")
    if payload.feed_url:
        ok, reason = validate_fetch_url(payload.feed_url)
        if not ok or not payload.feed_url.startswith("https://"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"feed_url must be a public https URL ({reason or 'https required'})")
    if payload.status == "ACTIVE" and not (payload.legal_approved and payload.security_approved):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "An ACTIVE source needs both legal and security approval")
    if payload.acquisition_mode == "PUBLIC_FETCH" and not payload.site_domain:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A PUBLIC_FETCH source needs its site_domain")

    row = db.scalar(select(SourceRightRegistry).where(SourceRightRegistry.source_id == source_id))
    before = None if row is None else f"{row.status} legal={row.legal_approved} security={row.security_approved}"
    if row is None:
        row = SourceRightRegistry(source_id=source_id)
        db.add(row)
    approvals_changed = row.legal_approved != payload.legal_approved or row.security_approved != payload.security_approved
    for field, value in payload.model_dump().items():
        setattr(row, field, value)
    if approvals_changed:
        row.terms_reviewed_at = datetime.now(_tz.utc)
    db.flush()
    log_external_search_event(
        db,
        action="source_registry.upserted",
        resource_type="source_right_registry",
        resource_id=source_id,
        correlation_id=get_correlation_id(request),
        reason=f"admin:{admin.id} before=[{before}] after=[{row.status} legal={row.legal_approved} security={row.security_approved}]",
    )
    db.commit()
    registry.invalidate()
    return {"source_id": source_id, "status": row.status, "active": registry.get(source_id) is not None}
