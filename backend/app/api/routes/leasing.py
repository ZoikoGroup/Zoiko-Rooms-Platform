from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_super_admin
from app.core.correlation import get_correlation_id
from app.crud import leasing as crud
from app.crud.audit import log_audit_event
from app.crud.eligibility import check_agreement_eligibility, check_offer_eligibility
from app.crud.events import emit_event
from app.crud.party import assert_provider_access, party_id_for_listing
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.services.booking_expiry import sweep_expired_offers
from app.schemas.leasing import (
    AgreementRead,
    AgreementSign,
    ApplicationCreate,
    ApplicationDecide,
    ApplicationRead,
    ApplicationUpdate,
    OfferRead,
    OfferTermsCreate,
    OfferTermsRead,
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
    terms = crud.add_offer_terms(db, offer, admin, payload)
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
def post_accept_offer(offer_id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    correlation_id = get_correlation_id(request)
    offer = crud.get_offer_or_404(db, offer_id, correlation_id=correlation_id)
    updated = crud.set_offer_status(db, offer, admin, "ACCEPTED", correlation_id=correlation_id)
    log_audit_event(db, admin, "offer.accept", "offer", str(offer_id), correlation_id)
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


@router.post("/offers/{offer_id}/agreement", response_model=AgreementRead)
def post_create_agreement(
    offer_id: int,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    correlation_id = get_correlation_id(request)
    offer = crud.get_offer_or_404(db, offer_id, correlation_id=correlation_id)
    agreement = crud.create_agreement(db, offer, admin)
    log_audit_event(db, admin, "agreement.create", "agreement", str(agreement.id), correlation_id)
    emit_event(db, "agreement.created", "agreement", str(agreement.id), {"offerId": offer_id})
    db.commit()
    return agreement


@router.get("/agreements/{agreement_id}", response_model=AgreementRead)
def get_agreement(agreement_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    agreement = crud.get_agreement_or_404(db, agreement_id)
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    return agreement


@router.get("/agreements/{agreement_id}/pdf")
def get_agreement_pdf(agreement_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    agreement = crud.get_agreement_or_404(db, agreement_id)
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    pdf_bytes = crud.generate_agreement_pdf(agreement)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="agreement-{agreement.id}.pdf"'},
    )


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
    agreement = crud.get_agreement_or_404(db, agreement_id)
    updated = crud.sign_agreement(db, agreement, payload.as_party, admin)
    log_audit_event(db, admin, "agreement.sign", "agreement", str(agreement_id), get_correlation_id(request), reason=payload.as_party)
    if updated.status == "SIGNED":
        emit_event(db, "agreement.signed", "agreement", str(agreement_id), {})
    db.commit()
    return updated


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
