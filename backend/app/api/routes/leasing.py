import json

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_super_admin
from app.core.agreement_documents import resolve_agreement_document_path
from app.core.correlation import get_correlation_id
from app.crud import agreement_amendments as amendment_crud
from app.crud import booking_change_requests as bcr_crud
from app.crud import agreement_clause_translations as translation_crud
from app.crud import agreement_clauses as clause_crud
from app.crud import agreement_form_templates as form_template_crud
from app.crud import leasing as crud
from app.crud import signature_provider as signature_provider_crud
from app.crud.audit import log_audit_event
from app.crud.eligibility import check_agreement_eligibility, check_offer_eligibility
from app.crud.events import emit_event
from app.crud.party import assert_provider_access, party_id_for_listing
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.listing_approval import CURRENT_POLICY_VERSION
from app.services.agreement_readiness import compute_host_readiness
from app.services.booking_expiry import sweep_expired_checkouts, sweep_expired_offers
from app.schemas.leasing import (
    AgreementAmendmentRead,
    AgreementCreateRequest,
    AgreementRead,
    AgreementSign,
    AmendmentClassifyRequest,
    AmendmentProposeTermsRequest,
    AmendmentRequestCreate,
    ApplicationCreate,
    ApplicationDecide,
    ApplicationRead,
    ApplicationUpdate,
    AgreementFormTemplateRead,
    BookingChangeDecisionRequest,
    BookingChangeRequestRead,
    ClauseDefinitionRead,
    ClauseDraftCreate,
    ClauseTranslationCreate,
    ClauseTranslationRead,
    DisclosureDeliverRequest,
    DisclosureRequirementRead,
    HostReadinessRead,
    MissingTranslationRead,
    OfferAcceptRequest,
    OfferRead,
    OfferTermsCreate,
    OfferTermsRead,
    SetSignatureProviderHealthRequest,
    SignatureEventRead,
    SignatureProviderCallbackRequest,
    SignatureProviderStatusRead,
    SignatureRequestRead,
)

router = APIRouter(prefix="/api/leasing", tags=["leasing"], dependencies=[Depends(get_current_admin)])


@router.get("/applications", response_model=list[ApplicationRead])
def get_applications(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return [crud.to_application_read(a) for a in crud.list_applications_for(db, admin)]


@router.post("/applications", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
def post_create_application(payload: ApplicationCreate, db: Session = Depends(get_db)):
    """Lets an admin record a test/demo application against their own listing --
    real applications normally arrive via the unauthenticated /api/public/applications
    endpoint from the separate renter-facing site, which doesn't exist yet."""
    return crud.to_application_read(crud.submit_application(db, payload))


@router.put("/applications/{application_id}", response_model=ApplicationRead)
def put_update_application(
    application_id: int,
    payload: ApplicationUpdate,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    application = crud.get_application_or_404(db, application_id)
    crud.update_application(db, application, admin, payload)
    log_audit_event(db, admin, "application.update", "application", str(application_id), get_correlation_id(request))
    db.commit()
    db.refresh(application)
    return crud.to_application_read(application)


@router.post("/applications/{application_id}/withdraw", response_model=ApplicationRead)
def post_withdraw_application(
    application_id: int,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    application = crud.get_application_or_404(db, application_id)
    crud.withdraw_application(db, application, admin)
    log_audit_event(db, admin, "application.withdraw", "application", str(application_id), get_correlation_id(request))
    emit_event(db, "application.withdrawn", "application", str(application_id), {})
    db.commit()
    db.refresh(application)
    return crud.to_application_read(application)


@router.post("/applications/{application_id}/decide", response_model=ApplicationRead, dependencies=[Depends(require_super_admin)])
def post_decide_application(
    application_id: int,
    payload: ApplicationDecide,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    application = crud.get_application_or_404(db, application_id)
    crud.decide_application(db, application, admin, payload)
    log_audit_event(db, admin, "application.decide", "application", str(application_id), get_correlation_id(request), reason=payload.decision)
    emit_event(db, "application.decided", "application", str(application_id), {"decision": payload.decision})
    db.commit()
    db.refresh(application)
    return crud.to_application_read(application)


@router.get("/applications/{application_id}/offer-eligibility")
def get_offer_eligibility(application_id: int, db: Session = Depends(get_db)):
    application = crud.get_application_or_404(db, application_id)
    reasons = check_offer_eligibility(db, application)
    return {"eligible": not reasons, "reasons": reasons}


@router.post("/applications/{application_id}/offers", response_model=OfferRead)
def post_create_offer(
    application_id: int,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    application = crud.get_application_or_404(db, application_id)
    offer = crud.create_offer(db, application, admin)
    log_audit_event(db, admin, "offer.create", "offer", str(offer.id), get_correlation_id(request))
    emit_event(db, "offer.created", "offer", str(offer.id), {"applicationId": application_id})
    db.commit()
    return offer


@router.get("/offers/{offer_id}", response_model=OfferRead)
def get_offer(offer_id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    offer = crud.get_offer_or_404(db, offer_id, correlation_id=get_correlation_id(request))
    assert_provider_access(db, admin, party_id_for_listing(offer.listing))
    return offer


@router.post("/offers/{offer_id}/terms", response_model=OfferTermsRead)
def post_offer_terms(
    offer_id: int,
    payload: OfferTermsCreate,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    correlation_id = get_correlation_id(request)
    offer = crud.get_offer_or_404(db, offer_id, correlation_id=correlation_id)
    terms = crud.add_offer_terms(db, offer, admin, payload, correlation_id=correlation_id)
    log_audit_event(db, admin, "offer.add_terms", "offer", str(offer_id), correlation_id)
    emit_event(db, "offer.terms_added", "offer", str(offer_id), {"version": terms.version, "monthlyRent": float(terms.monthly_rent)})
    db.commit()
    return terms


@router.post("/offers/{offer_id}/send", response_model=OfferRead)
def post_send_offer(offer_id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    correlation_id = get_correlation_id(request)
    offer = crud.get_offer_or_404(db, offer_id, correlation_id=correlation_id)
    updated = crud.set_offer_status(db, offer, admin, "SENT", correlation_id=correlation_id)
    log_audit_event(db, admin, "offer.send", "offer", str(offer_id), correlation_id)
    db.commit()
    return updated


@router.post("/offers/{offer_id}/accept", response_model=OfferRead)
def post_accept_offer(
    offer_id: int,
    request: Request,
    payload: OfferAcceptRequest = OfferAcceptRequest(),
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    correlation_id = get_correlation_id(request)
    offer = crud.get_offer_or_404(db, offer_id, correlation_id=correlation_id)
    before_state = offer.status
    updated = crud.set_offer_status(
        db, offer, admin, "ACCEPTED", correlation_id=correlation_id, override_reason=payload.override_reason,
    )
    log_audit_event(
        db, admin, "offer.accept", "offer", str(offer_id), correlation_id,
        reason=payload.override_reason or f"occupant_risk_tier={updated.occupant_risk_tier}",
        before_state=before_state, after_state=updated.status, policy_version=CURRENT_POLICY_VERSION,
    )
    emit_event(
        db, "offer.accepted", "offer", str(offer_id), {}, correlation_id=correlation_id,
        idempotency_key=f"offer.accepted:{offer_id}",
    )
    db.commit()
    return updated


@router.post("/offers/{offer_id}/decline", response_model=OfferRead)
def post_decline_offer(offer_id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    correlation_id = get_correlation_id(request)
    offer = crud.get_offer_or_404(db, offer_id, correlation_id=correlation_id)
    updated = crud.set_offer_status(db, offer, admin, "DECLINED", correlation_id=correlation_id)
    log_audit_event(db, admin, "offer.decline", "offer", str(offer_id), correlation_id)
    db.commit()
    return updated


@router.get("/offers/{offer_id}/agreement-eligibility")
def get_agreement_eligibility(offer_id: int, request: Request, db: Session = Depends(get_db)):
    offer = crud.get_offer_or_404(db, offer_id, correlation_id=get_correlation_id(request))
    reasons = check_agreement_eligibility(db, offer)
    return {"eligible": not reasons, "reasons": reasons}


@router.get("/offers/{offer_id}/agreement-readiness", response_model=HostReadinessRead)
def get_agreement_readiness(offer_id: int, request: Request, db: Session = Depends(get_db)):
    """ZR-ENG-CLR-004 Section 5.3: the Host template completion state for
    this offer's would-be agreement -- richer than agreement-eligibility
    above (which is just a pass/fail gate), reusing the exact same
    underlying checks so the two can never disagree."""
    offer = crud.get_offer_or_404(db, offer_id, correlation_id=get_correlation_id(request))
    return compute_host_readiness(db, offer)


@router.post("/offers/{offer_id}/agreement", response_model=AgreementRead)
def post_create_agreement(
    offer_id: int,
    request: Request,
    payload: AgreementCreateRequest = AgreementCreateRequest(),
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    correlation_id = get_correlation_id(request)
    offer = crud.get_offer_or_404(db, offer_id, correlation_id=correlation_id)
    agreement = crud.create_agreement(db, offer, admin, payload.selected_optional_clause_ids)
    log_audit_event(db, admin, "agreement.create", "agreement", str(agreement.id), correlation_id)
    emit_event(db, "agreement.created", "agreement", str(agreement.id), {"offerId": offer_id})
    db.commit()
    return agreement


@router.get("/agreements/{agreement_id}/disclosures", response_model=list[DisclosureRequirementRead])
def list_agreement_disclosures(
    agreement_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    agreement = crud.get_agreement_or_404(db, agreement_id)
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    return agreement.disclosures


@router.post("/agreements/{agreement_id}/disclosures/{disclosure_id}/deliver", response_model=DisclosureRequirementRead)
def post_deliver_disclosure(
    agreement_id: int,
    disclosure_id: int,
    request: Request,
    payload: DisclosureDeliverRequest = DisclosureDeliverRequest(),
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    agreement = crud.get_agreement_or_404(db, agreement_id)
    disclosure = crud.get_disclosure_or_404(db, agreement, disclosure_id)
    updated = crud.deliver_disclosure(db, agreement, disclosure, admin, to_party=payload.to_party, delivery_channel=payload.delivery_channel)
    log_audit_event(
        db, admin, "disclosure.deliver", "disclosure_requirement", str(disclosure_id), get_correlation_id(request),
        after_state=updated.status,
    )
    db.commit()
    return updated


@router.get("/agreements/{agreement_id}/disclosures/{disclosure_id}/document")
def get_disclosure_document(
    agreement_id: int, disclosure_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    agreement = crud.get_agreement_or_404(db, agreement_id)
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    disclosure = crud.get_disclosure_or_404(db, agreement, disclosure_id)
    if not disclosure.document_storage_ref:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This disclosure has not been delivered yet")
    pdf_bytes = resolve_agreement_document_path(disclosure.document_storage_ref).read_bytes()
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="disclosure-{disclosure.id}.pdf"'},
    )


@router.get("/agreements/{agreement_id}", response_model=AgreementRead)
def get_agreement(
    agreement_id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    agreement = crud.get_agreement_or_404(db, agreement_id)
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    log_audit_event(db, admin, "agreement.view", "agreement", str(agreement_id), get_correlation_id(request))
    db.commit()
    return agreement


@router.get("/agreements/{agreement_id}/pdf")
def get_agreement_pdf(
    agreement_id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-004 AC-07/AC-08: once a version is EXECUTED_IMMUTABLE, this
    serves the persisted, hash-verified artifact bytes -- never a fresh
    render of (possibly since-changed) live data. Before execution
    completes, still renders live from the working snapshot, clearly a draft
    since there's nothing executed yet to be immutable about."""
    agreement = crud.get_agreement_or_404(db, agreement_id)
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    log_audit_event(db, admin, "agreement.download", "agreement", str(agreement_id), get_correlation_id(request))
    db.commit()

    artifact = agreement.versions[-1].artifact if agreement.versions else None
    if artifact is not None:
        pdf_bytes = resolve_agreement_document_path(artifact.storage_ref).read_bytes()
    else:
        pdf_bytes = crud.generate_agreement_pdf(db, agreement)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="agreement-{agreement.id}.pdf"'},
    )


@router.get("/agreements/{agreement_id}/accessible-text")
def get_agreement_accessible_text(
    agreement_id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-004 AC-28: the screen-reader-friendly alternative to the
    PDF above -- same underlying frozen snapshot, plain text instead."""
    agreement = crud.get_agreement_or_404(db, agreement_id)
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    log_audit_event(db, admin, "agreement.download", "agreement", str(agreement_id), get_correlation_id(request), reason="accessible_text")
    db.commit()
    return Response(content=crud.generate_agreement_accessible_text(agreement), media_type="text/plain; charset=utf-8")


@router.get("/agreements/{agreement_id}/disclosures/{disclosure_id}/accessible-text")
def get_disclosure_accessible_text(
    agreement_id: int, disclosure_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    agreement = crud.get_agreement_or_404(db, agreement_id)
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    disclosure = crud.get_disclosure_or_404(db, agreement, disclosure_id)
    return Response(content=crud.generate_disclosure_accessible_text(disclosure), media_type="text/plain; charset=utf-8")


@router.post("/agreements/{agreement_id}/send", response_model=AgreementRead)
def post_send_agreement(
    agreement_id: int,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    agreement = crud.get_agreement_or_404(db, agreement_id)
    updated = crud.send_agreement(db, agreement, admin)
    log_audit_event(db, admin, "agreement.send", "agreement", str(agreement_id), get_correlation_id(request))
    db.commit()
    return updated


@router.post("/agreements/{agreement_id}/sign", response_model=AgreementRead)
def post_sign_agreement(
    agreement_id: int,
    payload: AgreementSign,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    correlation_id = get_correlation_id(request)
    agreement = crud.get_agreement_or_404(db, agreement_id, correlation_id=correlation_id)
    updated = crud.sign_agreement(db, agreement, payload.as_party, admin, method=payload.method, evidence_metadata=payload.evidence_metadata)
    log_audit_event(db, admin, "agreement.sign", "agreement", str(agreement_id), correlation_id, reason=payload.as_party)
    if updated.status == "SIGNED":
        emit_event(db, "agreement.signed", "agreement", str(agreement_id), {}, correlation_id=correlation_id)
    elif updated.status == "PAYMENT_IN_PROGRESS":
        emit_event(
            db, "agreement.payment_started", "agreement", str(agreement_id),
            {"payment_session_expires_at": updated.payment_session_expires_at.isoformat()},
            correlation_id=correlation_id,
        )
    db.commit()
    return updated


@router.get("/agreements/{agreement_id}/signature-events", response_model=list[SignatureEventRead])
def list_signature_events(
    agreement_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    agreement = crud.get_agreement_or_404(db, agreement_id)
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    return crud.list_signature_events(db, agreement)


@router.post("/agreements/{agreement_id}/sign/wet-ink", response_model=AgreementRead)
def post_sign_agreement_wet_ink(
    agreement_id: int,
    request: Request,
    as_party: str,
    scan: UploadFile = File(...),
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-004 AC-11 wet-ink fallback: admin uploads a scanned signed
    document as evidence instead of the in-app click-to-sign flow. Scoped to
    PDF scans only -- see core/agreement_documents.py, which always persists
    under a .pdf name."""
    if scan.content_type != "application/pdf":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Wet-ink scan must be a PDF")
    correlation_id = get_correlation_id(request)
    agreement = crud.get_agreement_or_404(db, agreement_id, correlation_id=correlation_id)
    scan_bytes = scan.file.read()
    updated = crud.record_wet_ink_signature(db, agreement, as_party, scan_bytes, admin)
    log_audit_event(
        db, admin, "agreement.sign", "agreement", str(agreement_id), correlation_id,
        reason=f"{as_party}:WET_INK",
    )
    if updated.status == "SIGNED":
        emit_event(db, "agreement.signed", "agreement", str(agreement_id), {}, correlation_id=correlation_id)
    elif updated.status == "PAYMENT_IN_PROGRESS":
        emit_event(
            db, "agreement.payment_started", "agreement", str(agreement_id),
            {"payment_session_expires_at": updated.payment_session_expires_at.isoformat()},
            correlation_id=correlation_id,
        )
    db.commit()
    return updated


@router.get("/agreement-clauses", response_model=list[ClauseDefinitionRead], dependencies=[Depends(require_super_admin)])
def get_agreement_clauses(clause_id: str | None = None, db: Session = Depends(get_db)):
    """ZR-ENG-CLR-004 AC-25/Section 12 'Change legal template: ... Legal
    content governance only' -- the full version history for one clause_id
    (or every clause_id, unfiltered), so an admin can see what's currently
    active vs retired vs still draft."""
    return clause_crud.list_clause_versions(db, clause_id)


@router.post("/agreement-clauses", response_model=ClauseDefinitionRead, dependencies=[Depends(require_super_admin)])
def post_create_agreement_clause(
    payload: ClauseDraftCreate, request: Request, admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    row = clause_crud.create_clause_draft(
        db, admin, clause_id=payload.clause_id, jurisdiction_scope=payload.jurisdiction_scope,
        agreement_class=payload.agreement_class, mandatory_level=payload.mandatory_level,
        title=payload.title, approval_note=payload.approval_note,
    )
    log_audit_event(
        db, admin, "agreement_clause.draft_create", "agreement_clause_definition", str(row.id), get_correlation_id(request),
        reason=f"{row.clause_id} v{row.version}",
    )
    db.commit()
    return row


@router.post(
    "/agreement-clauses/{clause_definition_id}/approve", response_model=ClauseDefinitionRead,
    dependencies=[Depends(require_super_admin)],
)
def post_approve_agreement_clause(
    clause_definition_id: int, request: Request, admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    row = clause_crud.get_clause_version_or_404(db, clause_definition_id)
    updated = clause_crud.approve_clause_version(db, admin, row)
    log_audit_event(
        db, admin, "agreement_clause.approve", "agreement_clause_definition", str(updated.id), get_correlation_id(request),
        reason=f"{updated.clause_id} v{updated.version}",
    )
    db.commit()
    return updated


@router.post(
    "/agreement-clauses/{clause_definition_id}/rollback", response_model=ClauseDefinitionRead,
    dependencies=[Depends(require_super_admin)],
)
def post_rollback_agreement_clause(
    clause_definition_id: int, request: Request, admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    row = clause_crud.get_clause_version_or_404(db, clause_definition_id)
    updated = clause_crud.rollback_clause(db, admin, row)
    log_audit_event(
        db, admin, "agreement_clause.rollback", "agreement_clause_definition", str(updated.id), get_correlation_id(request),
        reason=f"{updated.clause_id} v{updated.version}",
    )
    db.commit()
    return updated


@router.get(
    "/agreement-form-templates", response_model=list[AgreementFormTemplateRead],
    dependencies=[Depends(require_super_admin)],
)
def get_agreement_form_templates(
    jurisdiction_scope: str | None = None, agreement_class: str | None = None, form_mode: str | None = None,
    db: Session = Depends(get_db),
):
    return form_template_crud.list_form_templates(
        db, jurisdiction_scope=jurisdiction_scope, agreement_class=agreement_class, form_mode=form_mode,
    )


@router.post(
    "/agreement-form-templates", response_model=AgreementFormTemplateRead, dependencies=[Depends(require_super_admin)],
)
def post_create_agreement_form_template(
    request: Request,
    jurisdiction_scope: str, agreement_class: str, form_mode: str, title: str,
    field_anchor_map_json: str = "{}", authoritative_content_text: str = "", approval_note: str = "",
    source_document: UploadFile | None = File(None),
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-004 AC-04: create a new DRAFT template version for a
    prescribed-form mode. Modes B/D require an uploaded source document
    (the blank official form for B, the whole external agreement for D);
    mode C instead takes authoritative_content_text (the reference text
    generation is diff-checked against)."""
    field_anchor_map = json.loads(field_anchor_map_json) if field_anchor_map_json else {}
    source_bytes = source_document.file.read() if source_document is not None else None
    row = form_template_crud.create_form_template_draft(
        db, admin, jurisdiction_scope=jurisdiction_scope, agreement_class=agreement_class, form_mode=form_mode,
        title=title, field_anchor_map=field_anchor_map, authoritative_content_text=authoritative_content_text,
        approval_note=approval_note, source_document_bytes=source_bytes,
    )
    log_audit_event(
        db, admin, "agreement_form_template.draft_create", "agreement_form_template", str(row.id), get_correlation_id(request),
        reason=f"{jurisdiction_scope}:{agreement_class}:{form_mode} v{row.version}",
    )
    db.commit()
    return row


@router.post(
    "/agreement-form-templates/{template_id}/approve", response_model=AgreementFormTemplateRead,
    dependencies=[Depends(require_super_admin)],
)
def post_approve_agreement_form_template(
    template_id: int, request: Request, admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    row = form_template_crud.get_form_template_or_404(db, template_id)
    updated = form_template_crud.approve_form_template(db, admin, row)
    log_audit_event(
        db, admin, "agreement_form_template.approve", "agreement_form_template", str(updated.id), get_correlation_id(request),
    )
    db.commit()
    return updated


@router.post(
    "/agreement-form-templates/{template_id}/rollback", response_model=AgreementFormTemplateRead,
    dependencies=[Depends(require_super_admin)],
)
def post_rollback_agreement_form_template(
    template_id: int, request: Request, admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    row = form_template_crud.get_form_template_or_404(db, template_id)
    updated = form_template_crud.rollback_form_template(db, admin, row)
    log_audit_event(
        db, admin, "agreement_form_template.rollback", "agreement_form_template", str(updated.id), get_correlation_id(request),
    )
    db.commit()
    return updated


@router.get(
    "/agreement-clauses/{clause_definition_id}/translations", response_model=list[ClauseTranslationRead],
    dependencies=[Depends(require_super_admin)],
)
def get_clause_translations(clause_definition_id: int, db: Session = Depends(get_db)):
    return translation_crud.list_translations_for_clause(db, clause_definition_id)


@router.post(
    "/agreement-clauses/{clause_definition_id}/translations", response_model=ClauseTranslationRead,
    dependencies=[Depends(require_super_admin)],
)
def post_create_clause_translation(
    clause_definition_id: int, payload: ClauseTranslationCreate, request: Request,
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    row = translation_crud.create_translation(
        db, admin, clause_definition_id=clause_definition_id, language_code=payload.language_code,
        translated_title=payload.translated_title, translated_content=payload.translated_content,
    )
    log_audit_event(
        db, admin, "agreement_clause_translation.create", "clause_translation", str(row.id), get_correlation_id(request),
        reason=payload.language_code,
    )
    db.commit()
    return row


@router.post(
    "/agreement-clause-translations/{translation_id}/approve", response_model=ClauseTranslationRead,
    dependencies=[Depends(require_super_admin)],
)
def post_approve_clause_translation(
    translation_id: int, request: Request, admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    translation = translation_crud.get_translation_or_404(db, translation_id)
    updated = translation_crud.approve_translation(db, admin, translation)
    log_audit_event(
        db, admin, "agreement_clause_translation.approve", "clause_translation", str(translation_id), get_correlation_id(request),
    )
    db.commit()
    return updated


@router.get(
    "/agreement-clauses/missing-translations", response_model=list[MissingTranslationRead],
    dependencies=[Depends(require_super_admin)],
)
def get_missing_translations(language_code: str, db: Session = Depends(get_db)):
    missing = translation_crud.missing_translations_for_effective_clauses(db, language_code)
    return [
        MissingTranslationRead(clause_definition_id=c.id, clause_id=c.clause_id, version=c.version, title=c.title)
        for c in missing
    ]


@router.post("/agreements/{agreement_id}/amendments", response_model=AgreementAmendmentRead)
def post_request_amendment(
    agreement_id: int, payload: AmendmentRequestCreate, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    agreement = crud.get_agreement_or_404(db, agreement_id)
    amendment = amendment_crud.request_amendment(db, agreement, admin, payload.reason)
    log_audit_event(db, admin, "agreement_amendment.request", "agreement_amendment", str(amendment.id), get_correlation_id(request))
    db.commit()
    return amendment


@router.get("/agreements/{agreement_id}/amendments", response_model=list[AgreementAmendmentRead])
def get_agreement_amendments(agreement_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    agreement = crud.get_agreement_or_404(db, agreement_id)
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    return amendment_crud.list_amendments(db, agreement)


@router.post("/agreements/{agreement_id}/amendments/{amendment_id}/classify", response_model=AgreementAmendmentRead)
def post_classify_amendment(
    agreement_id: int, amendment_id: int, payload: AmendmentClassifyRequest, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    agreement = crud.get_agreement_or_404(db, agreement_id)
    amendment = amendment_crud.get_amendment_or_404(db, agreement, amendment_id)
    updated = amendment_crud.classify_amendment(db, amendment, admin, payload.amendment_type)
    log_audit_event(
        db, admin, "agreement_amendment.classify", "agreement_amendment", str(amendment_id), get_correlation_id(request),
        reason=payload.amendment_type,
    )
    db.commit()
    return updated


@router.post("/agreements/{agreement_id}/amendments/{amendment_id}/propose-terms", response_model=AgreementAmendmentRead)
def post_propose_amendment_terms(
    agreement_id: int, amendment_id: int, payload: AmendmentProposeTermsRequest, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    agreement = crud.get_agreement_or_404(db, agreement_id)
    amendment = amendment_crud.get_amendment_or_404(db, agreement, amendment_id)
    updated = amendment_crud.propose_terms(db, amendment, admin, payload.proposed_terms)
    log_audit_event(db, admin, "agreement_amendment.propose_terms", "agreement_amendment", str(amendment_id), get_correlation_id(request))
    db.commit()
    return updated


@router.post(
    "/agreements/{agreement_id}/amendments/{amendment_id}/approve", response_model=AgreementAmendmentRead,
    dependencies=[Depends(require_super_admin)],
)
def post_approve_amendment(
    agreement_id: int, amendment_id: int, request: Request,
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    agreement = crud.get_agreement_or_404(db, agreement_id)
    amendment = amendment_crud.get_amendment_or_404(db, agreement, amendment_id)
    correlation_id = get_correlation_id(request)
    updated = amendment_crud.approve_amendment(db, amendment, admin, correlation_id=correlation_id)
    return updated


@router.get("/booking-change-requests", response_model=list[BookingChangeRequestRead])
def get_booking_change_requests(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return [bcr_crud.to_booking_change_request_read(b) for b in bcr_crud.list_change_requests_for_admin(db, admin)]


@router.post(
    "/booking-change-requests/{bcr_id}/approve", response_model=BookingChangeRequestRead,
    dependencies=[Depends(require_super_admin)],
)
def post_approve_booking_change_request(
    bcr_id: int, payload: BookingChangeDecisionRequest, request: Request,
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    bcr = bcr_crud.get_booking_change_request_or_404(db, bcr_id)
    updated = bcr_crud.approve_change_request(db, bcr, admin, decision_note=payload.decision_note)
    log_audit_event(db, admin, "booking_change_request.approve", "booking_change_request", str(bcr_id), get_correlation_id(request))
    db.commit()
    return bcr_crud.to_booking_change_request_read(updated)


@router.post("/booking-change-requests/{bcr_id}/decline", response_model=BookingChangeRequestRead)
def post_decline_booking_change_request(
    bcr_id: int, payload: BookingChangeDecisionRequest, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    bcr = bcr_crud.get_booking_change_request_or_404(db, bcr_id)
    updated = bcr_crud.decline_change_request(db, bcr, admin, decision_note=payload.decision_note)
    log_audit_event(db, admin, "booking_change_request.decline", "booking_change_request", str(bcr_id), get_correlation_id(request))
    db.commit()
    return bcr_crud.to_booking_change_request_read(updated)


@router.get("/agreements/{agreement_id}/signature-requests", response_model=list[SignatureRequestRead])
def get_agreement_signature_requests(agreement_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    agreement = crud.get_agreement_or_404(db, agreement_id)
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    from sqlalchemy import select as _select
    from app.models.signature_provider import SignatureRequest as _SignatureRequest
    return list(db.scalars(_select(_SignatureRequest).where(_SignatureRequest.agreement_id == agreement.id).order_by(_SignatureRequest.created_at)))


@router.post("/agreements/{agreement_id}/signature-requests/{signature_request_id}/dispatch", response_model=SignatureRequestRead)
def post_dispatch_signature_request(
    agreement_id: int, signature_request_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    agreement = crud.get_agreement_or_404(db, agreement_id)
    signature_request = signature_provider_crud.get_signature_request_or_404(db, agreement, signature_request_id)
    return signature_provider_crud.dispatch_signature_request(db, agreement, signature_request, admin)


@router.post("/agreements/{agreement_id}/signature-webhooks/simulate", response_model=SignatureRequestRead)
def post_simulate_signature_webhook(
    agreement_id: int, payload: SignatureProviderCallbackRequest,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-004 AC-23/AC-24: stands in for the provider's own webhook
    call -- see models/signature_provider.py's module docstring for why this
    is an authenticated admin action rather than a public unauthenticated
    route in this codebase. Idempotent by provider_event_id."""
    agreement = crud.get_agreement_or_404(db, agreement_id)
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    return signature_provider_crud.ingest_provider_callback(
        db, agreement, admin, provider_event_id=payload.provider_event_id,
        provider_transaction_id=payload.provider_transaction_id, event_type=payload.event_type,
    )


@router.get("/signature-provider/status", response_model=SignatureProviderStatusRead, dependencies=[Depends(require_super_admin)])
def get_signature_provider_status(db: Session = Depends(get_db)):
    return signature_provider_crud.get_or_create_provider_status(db)


@router.post("/signature-provider/health", response_model=SignatureProviderStatusRead, dependencies=[Depends(require_super_admin)])
def post_set_signature_provider_health(
    payload: SetSignatureProviderHealthRequest, admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    return signature_provider_crud.set_provider_health(db, admin, payload.healthy)


@router.post("/signature-requests/reconcile-stalled", dependencies=[Depends(require_super_admin)])
def post_reconcile_stalled_signature_requests(admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db)):
    stalled = signature_provider_crud.reconcile_stalled_signature_requests(db)
    return {"failedCount": len(stalled), "signatureRequestIds": [sr.id for sr in stalled]}


@router.post("/offers/expire-overdue", dependencies=[Depends(require_super_admin)])
def post_expire_overdue_offers(
    request: Request, admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-001 Rule 7: manual substitute for a cron tick -- no
    scheduler exists in this stack (same limitation as
    list_occupancies_missing_upcoming_rent). Individual offers already
    self-heal to EXPIRED lazily on read (get_offer_or_404); this bulk sweep
    is for ops visibility and for a room to be freed even if nobody happens
    to read that specific offer again."""
    correlation_id = get_correlation_id(request)
    expired = sweep_expired_offers(db, correlation_id=correlation_id)
    log_audit_event(
        db, admin, "offer.expire_overdue_sweep", "offer", "bulk", correlation_id,
        reason=f"expired {len(expired)} offer(s)",
    )
    db.commit()
    return {"expiredCount": len(expired), "expiredOfferIds": [o.id for o in expired]}


@router.post("/agreements/expire-overdue-checkouts", dependencies=[Depends(require_super_admin)])
def post_expire_overdue_checkouts(
    request: Request, admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-001 Rule 7 (10.2): manual substitute for a cron tick, same
    shape as post_expire_overdue_offers above -- individual agreements
    already self-heal lazily on read (get_agreement_or_404); this bulk sweep
    is for ops visibility and so a stuck checkout is freed even if nobody
    happens to read that specific agreement again."""
    correlation_id = get_correlation_id(request)
    expired = sweep_expired_checkouts(db, correlation_id=correlation_id)
    log_audit_event(
        db, admin, "agreement.expire_overdue_checkout_sweep", "agreement", "bulk", correlation_id,
        reason=f"expired {len(expired)} checkout(s)",
    )
    db.commit()
    return {"expiredCount": len(expired), "expiredAgreementIds": [a.id for a in expired]}
