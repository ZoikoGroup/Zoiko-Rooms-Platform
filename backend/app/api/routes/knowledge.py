"""Admin Knowledge Base management routes (Phase 5).

Covers source registration/ingestion, chunk inspection, and the release
governance steps available today: create (ingest+chunk), approve -> ACTIVE,
attach to a release, and revoke. Real maker-checker gate steps (separate
reviewer approval, immutable release manifest) remain future work, so the
endpoints here are marked as the engineering-visible subset.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_super_admin
from app.core.correlation import get_correlation_id
from app.crud.audit import log_audit_event
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.kb import (
    KB_ACCESS_CLASSES,
    KB_DOMAINS,
    KB_MARKETS,
    KbChunk,
    KbDocument,
    KbRelease,
)
from app.services.kb import KnowledgeError, ingest_document, make_active, revoke_document

router = APIRouter(prefix="/api/admin/knowledge", tags=["knowledge-base"], dependencies=[Depends(get_current_admin)])


def _doc_to_dict(doc: KbDocument) -> dict:
    return {
        "id": doc.id,
        "slug": doc.slug,
        "title": doc.title,
        "market": doc.market,
        "jurisdiction": doc.jurisdiction,
        "domain": doc.domain,
        "accessClass": doc.access_class,
        "status": doc.status,
        "releaseId": doc.release_id,
        "effectiveDate": doc.effective_date.isoformat() if doc.effective_date else None,
        "expiryDate": doc.expiry_date.isoformat() if doc.expiry_date else None,
    }


@router.post("/documents", status_code=status.HTTP_201_CREATED)
def create_document(
    payload: dict,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    slug = (payload.get("slug") or "").strip()
    title = (payload.get("title") or "").strip()
    content = payload.get("content") or ""
    if not slug or not title or not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "slug, title and content are required")
    try:
        result = ingest_document(
            db,
            slug=slug,
            title=title,
            content=content,
            market=payload.get("market", "GLOBAL"),
            jurisdiction=payload.get("jurisdiction", ""),
            domain=payload.get("domain", "general"),
            access_class=payload.get("accessClass", "K0_PUBLIC"),
            trust_tier=int(payload.get("trustTier", 1)),
            author=admin.full_name,
            owner=admin.email,
        )
    except KnowledgeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    db.commit()
    log_audit_event(
        db,
        admin,
        "knowledge.document.created",
        "kb_document",
        str(result.document_id),
        get_correlation_id(request),
        reason=f"status:{result.status} chunks:{result.chunk_count} quarantine:{','.join(result.quarantine_reasons)}",
    )
    doc = db.get(KbDocument, result.document_id)
    return {"document": _doc_to_dict(doc), "status": result.status, "chunkCount": result.chunk_count}


@router.get("/documents")
def list_documents(
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    docs = db.scalars(select(KbDocument).order_by(KbDocument.created_at.desc()).limit(200))
    return [_doc_to_dict(d) for d in docs]


@router.get("/documents/{document_id}/chunks")
def get_chunks(
    document_id: int,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    chunks = db.scalars(select(KbChunk).where(KbChunk.document_id == document_id).order_by(KbChunk.chunk_index))
    return [{"index": c.chunk_index, "section": c.section, "content": c.content} for c in chunks]


@router.post("/documents/{document_id}/activate")
def activate_document(
    document_id: int,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    try:
        doc = make_active(db, document_id)
    except KnowledgeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    db.commit()
    log_audit_event(
        db,
        admin,
        "knowledge.document.activated",
        "kb_document",
        str(document_id),
        get_correlation_id(request),
    )
    return _doc_to_dict(doc)


@router.post("/releases")
def create_release(
    payload: dict,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Create a NEW DRAFT release (maker step). Activation attaches documents."""
    version = (payload.get("version") or "").strip()
    market = payload.get("market", "GLOBAL")
    if not version:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "version is required")
    if market not in KB_MARKETS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"invalid market: {market}")
    release = KbRelease(version=version, market=market, status="DRAFT")
    db.add(release)
    db.flush()
    log_audit_event(
        db,
        admin,
        "knowledge.release.created",
        "kb_release",
        str(release.id),
        get_correlation_id(request),
        reason=f"version:{version} market:{market}",
    )
    db.commit()
    return {"id": release.id, "version": release.version, "market": release.market, "status": release.status}


@router.post("/releases/{release_id}/activate")
def activate_release(
    release_id: int,
    payload: dict,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Attach ACTIVE documents to a release and activate it (checker step)."""
    release = db.get(KbRelease, release_id)
    if release is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Release not found")
    if release.status == "REVOKED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Revoked releases cannot be reactivated")
    document_ids = payload.get("documentIds") or []
    if not document_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "documentIds required to activate a release")
    for did in document_ids:
        doc = db.get(KbDocument, int(did))
        if doc is None or doc.status != "ACTIVE":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"document {did} is not ACTIVE",
            )
        if doc.market not in ("GLOBAL", release.market):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"document {did} market {doc.market} incompatible with release {release.market}",
            )
        doc.release_id = release.id
    release.status = "ACTIVE"
    release.activated_at = datetime.now(timezone.utc)
    db.flush()
    log_audit_event(
        db,
        admin,
        "knowledge.release.activated",
        "kb_release",
        str(release_id),
        get_correlation_id(request),
        reason=f"version:{release.version} docs:{len(document_ids)}",
    )
    db.commit()
    return {
        "id": release.id,
        "version": release.version,
        "market": release.market,
        "status": release.status,
        "activatedAt": release.activated_at.isoformat() if release.activated_at else None,
        "documentCount": len(document_ids),
    }


@router.post("/documents/{document_id}/revoke")
def revoke(
    document_id: int,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    try:
        doc = revoke_document(db, document_id)
    except KnowledgeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    db.commit()
    log_audit_event(
        db,
        admin,
        "knowledge.document.revoked",
        "kb_document",
        str(document_id),
        get_correlation_id(request),
    )
    return _doc_to_dict(doc)


@router.get("/taxonomies")
def taxonomies(admin: AdminUser = Depends(get_current_admin)):
    return {
        "markets": list(KB_MARKETS),
        "accessClasses": list(KB_ACCESS_CLASSES),
        "domains": list(KB_DOMAINS),
    }
