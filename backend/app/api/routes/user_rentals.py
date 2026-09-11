import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.agreement_documents import resolve_agreement_document_path
from app.core.correlation import get_correlation_id
from app.core.identity_uploads import resolve_identity_document_path
from app.crud import booking_change_requests as bcr_crud
from app.crud import finance as finance_crud
from app.crud import leasing as leasing_crud
from app.crud import occupancy as occupancy_crud
from app.crud import review as review_crud
from app.crud import sublet as sublet_crud
from app.crud.listing import assert_party_does_not_own_listing
from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.crud.eligibility import check_offer_eligibility
from app.crud.guest import get_guest_for_user, get_or_create_guest_for_user
from app.crud.identity_verification import get_verified_identity_for_party
from app.crud import notification as notif_crud
from app.crud.user import get_user_by_email, get_user_by_party_id
from app.db.session import get_db
from app.models.leasing import Application
from app.models.listing import Listing
from app.models.listing_approval import CURRENT_POLICY_VERSION
from app.models.occupancy import Occupancy
from app.services.booking_expiry import expire_offer_if_overdue
from app.models.user_account import UserAccount
from app.schemas.finance import DepositClaimItemRead, DepositClaimItemRespond, DepositClaimRead
from app.schemas.leasing import (
    AgreementRead,
    BookingChangeRequestCreate,
    BookingChangeRequestRead,
    DisclosureRequirementRead,
    ExtensionRequestCreate,
    FinancialChangeRequestCreate,
    PremisesChangeRequestCreate,
    ShorteningRequestCreate,
    OfferAcceptRequest,
    OfferRead,
    SubletRenterLookup,
    SubletRequestCreate,
    SubletRequestRead,
    UserAgreementSignRequest,
    UserApplicationRead,
    UserApplicationSubmitRequest,
    UserOccupancyRead,
)
from app.schemas.activation_gate import HandoverEventCreate, HandoverEventRead
from app.schemas.review import ReviewCreate, ReviewRead

router = APIRouter(prefix="/api/users/rentals", tags=["user-rentals"], dependencies=[Depends(get_current_user)])

logger = logging.getLogger("zoiko.user_rentals")


def _property_and_host(db: Session, listing: Listing | None) -> tuple[str, str, str]:
    """Resolves (property_address, property_city, host_name) for a listing's
    room/property so renter-facing screens can show where they're
    applying/living and who the host is, without exposing internal party ids."""
    if listing is None or listing.room is None or listing.room.property is None:
        return "", "", ""
    property_ = listing.room.property
    host = get_user_by_party_id(db, property_.owner_party_id)
    return property_.address, property_.city, host.full_name if host else ""


def _to_user_application_read(db: Session, application: Application) -> UserApplicationRead:
    """listing_name is looked up here (not stored on Application) so it always
    reflects the listing's current name; falls back to "" if the listing was
    since deleted, which the frontend renders gracefully.

    offer_status/agreement_status are surfaced here too -- application.status
    alone is stuck on "DECIDED" for the rest of the lifecycle, so without these
    the applicant can't tell an approved-but-nothing-sent-yet application apart
    from one whose agreement is fully signed."""
    listing = db.get(Listing, application.listing_id)
    property_address, property_city, host_name = _property_and_host(db, listing)
    offer = application.offer
    agreement = offer.agreement if offer else None
    return UserApplicationRead(
        id=application.id,
        listing_id=application.listing_id,
        listing_name=listing.name if listing else "",
        property_address=property_address,
        property_city=property_city,
        host_name=host_name,
        status=application.status,
        message=application.message,
        desired_move_in=application.desired_move_in,
        submitted_at=application.submitted_at,
        updated_at=application.updated_at,
        offer_id=offer.id if offer else None,
        offer_status=offer.status if offer else None,
        agreement_id=agreement.id if agreement else None,
        agreement_status=agreement.status if agreement else None,
    )


def _to_user_occupancy_read(db: Session, occupancy: Occupancy) -> UserOccupancyRead:
    listing = db.get(Listing, occupancy.listing_id)
    property_address, property_city, host_name = _property_and_host(db, listing)
    agreement = occupancy.offer.agreement if occupancy.offer else None
    return UserOccupancyRead(
        id=occupancy.id,
        listing_id=occupancy.listing_id,
        listing_name=listing.name if listing else "",
        room_id=occupancy.room_id,
        property_address=property_address,
        property_city=property_city,
        host_name=host_name,
        status=occupancy.status,
        move_in_date=occupancy.move_in_date,
        expected_end_date=occupancy.expected_end_date,
        move_out_date=occupancy.move_out_date,
        created_at=occupancy.created_at,
        ended_at=occupancy.ended_at,
        agreement_id=agreement.id if agreement else None,
    )


@router.post("/applications", response_model=UserApplicationRead, status_code=status.HTTP_201_CREATED)
def submit_rental_application(
    payload: UserApplicationSubmitRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User submits a rental application for a listing.
    Requires verified identity before application can proceed.
    """
    # Check identity verification
    if not user.party_id or not get_verified_identity_for_party(db, user.party_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You must complete identity verification before submitting applications",
        )

    # A host cannot apply to their own listing -- enforced here regardless of
    # what the frontend shows, so it can't be bypassed by calling the API directly.
    listing = db.get(Listing, payload.listing_id)
    if listing and user.party_id:
        assert_party_does_not_own_listing(listing, user.party_id)

    # Get or create guest for this user
    guest = get_or_create_guest_for_user(db, user)

    # Submit application using existing CRUD logic
    from app.schemas.leasing import ApplicationCreate

    app_data = ApplicationCreate(
        listing_id=payload.listing_id, guest_id=guest.id, message=payload.message,
        desired_move_in=payload.desired_move_in, named_occupant_guest_id=payload.named_occupant_guest_id,
    )

    try:
        application = leasing_crud.submit_application(db, app_data)
        log_audit_event(db, None, "user_application.submit", "application", str(application.id), get_correlation_id(request), reason=f"user:{user.id}")

        listing = db.get(Listing, application.listing_id)
        notif_crud.notify_user(
            db, user.id,
            title="Application submitted",
            message=f'Your application for "{listing.name if listing else application.listing_id}" has been submitted.',
            notification_type="application.confirmation",
            related_entity_type="application", related_entity_id=str(application.id),
        )
        if listing and listing.party_id:
            notif_crud.notify_user_by_party(
                db, listing.party_id,
                title="New rental application",
                message=f"{user.full_name} applied for your listing \"{listing.name}\".",
                notification_type="application.received",
                related_entity_type="application", related_entity_id=str(application.id),
            )
        notif_crud.notify_all_super_admins(
            db,
            title="New rental application submitted",
            message=f"{user.full_name} applied for listing {application.listing_id}.",
            notification_type="application.submitted",
            related_entity_type="application", related_entity_id=str(application.id),
        )

        db.commit()

        return _to_user_application_read(db, application)
    except HTTPException:
        # A deliberate, safe error from submit_application (e.g. 404/409) --
        # pass it through unchanged instead of flattening it to a 400.
        raise
    except Exception:
        logger.exception("rental application submission failed for user %s", user.id)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not submit your application. Please try again.")


@router.get("/applications", response_model=list[UserApplicationRead])
def list_user_applications(
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all rental applications submitted by current user."""
    if not user.party_id:
        return []

    # Get guest associated with this user
    guest = get_guest_for_user(db, user)
    if not guest:
        return []

    applications = list(
        db.scalars(
            select(Application)
            .where(Application.guest_id == guest.id)
            .order_by(Application.submitted_at.desc())
        )
    )

    return [_to_user_application_read(db, app) for app in applications]


@router.get("/applications/{application_id}", response_model=UserApplicationRead)
def get_application_details(
    application_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get details of a specific application."""
    application = db.get(Application, application_id)
    if not application:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")

    # Verify the application belongs to this user
    guest = get_guest_for_user(db, user)
    if not guest or application.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view your own applications")

    return _to_user_application_read(db, application)


@router.post("/applications/{application_id}/withdraw", response_model=UserApplicationRead)
def withdraw_application(
    application_id: int,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User withdraws a rental application."""
    application = db.get(Application, application_id)
    if not application:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")

    # Verify ownership
    guest = get_guest_for_user(db, user)
    if not guest or application.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only withdraw your own applications")

    if application.status != "SUBMITTED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Can only withdraw applications in SUBMITTED status")

    application.status = "WITHDRAWN"
    application.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(application)

    log_audit_event(
        db,
        None,
        "user_application.withdraw",
        "application",
        str(application_id),
        get_correlation_id(request),
        reason=f"user:{user.id}",
    )
    db.commit()

    return _to_user_application_read(db, application)


@router.get("/applications/{application_id}/offer", response_model=OfferRead)
def get_own_offer(
    application_id: int,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read-only view of the offer (and its terms/agreement) for one of the
    user's own applications -- previously the applicant had no way to see
    their own offer at all; every step was admin-only."""
    application = db.get(Application, application_id)
    if not application:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    guest = get_guest_for_user(db, user)
    if not guest or application.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view your own applications")
    if not application.offer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No offer has been made for this application yet")
    offer = application.offer
    if expire_offer_if_overdue(db, offer, correlation_id=get_correlation_id(request)):
        db.commit()
        db.refresh(offer)
    return offer


@router.post("/offers/{offer_id}/accept", response_model=OfferRead)
def accept_own_offer(
    offer_id: int,
    request: Request,
    payload: OfferAcceptRequest = OfferAcceptRequest(),
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    correlation_id = get_correlation_id(request)
    offer = leasing_crud.get_offer_or_404(db, offer_id, correlation_id=correlation_id)
    before_state = offer.status
    updated = leasing_crud.user_accept_offer(
        db, user, offer, correlation_id=correlation_id, override_reason=payload.override_reason,
    )
    log_audit_event(
        db, None, "user_offer.accept", "offer", str(offer_id), correlation_id,
        reason=payload.override_reason or f"user:{user.id}; occupant_risk_tier={updated.occupant_risk_tier}",
        before_state=before_state, after_state=updated.status, policy_version=CURRENT_POLICY_VERSION,
    )
    emit_event(
        db, "offer.accepted", "offer", str(offer_id), {}, correlation_id=correlation_id,
        idempotency_key=f"offer.accepted:{offer_id}",
    )
    db.commit()
    return updated


@router.post("/offers/{offer_id}/decline", response_model=OfferRead)
def decline_own_offer(
    offer_id: int,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    correlation_id = get_correlation_id(request)
    offer = leasing_crud.get_offer_or_404(db, offer_id, correlation_id=correlation_id)
    updated = leasing_crud.user_decline_offer(db, user, offer, correlation_id=correlation_id)
    log_audit_event(db, None, "user_offer.decline", "offer", str(offer_id), correlation_id, reason=f"user:{user.id}")
    db.commit()
    return updated


@router.post("/agreements/{agreement_id}/sign", response_model=AgreementRead)
def sign_own_agreement(
    agreement_id: int,
    request: Request,
    payload: UserAgreementSignRequest = UserAgreementSignRequest(),
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The renter signing their own agreement -- previously every signature,
    including the renter's, was recorded by an admin attesting on their behalf."""
    correlation_id = get_correlation_id(request)
    agreement = leasing_crud.get_agreement_or_404(db, agreement_id, correlation_id=correlation_id)
    updated = leasing_crud.user_sign_agreement(db, user, agreement, method=payload.method, evidence_metadata=payload.evidence_metadata)
    log_audit_event(db, None, "user_agreement.sign", "agreement", str(agreement_id), correlation_id, reason=f"user:{user.id}")
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


@router.post("/agreements/{agreement_id}/change-requests", response_model=BookingChangeRequestRead, status_code=status.HTTP_201_CREATED)
def request_own_move_in_date_change(
    agreement_id: int,
    payload: BookingChangeRequestCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-008 Section 8 MVP: a renter requesting a new move-in date
    before they've moved in. Only DATE_SHIFT on a fully signed agreement is
    supported so far."""
    correlation_id = get_correlation_id(request)
    agreement = leasing_crud.get_agreement_or_404(db, agreement_id, correlation_id=correlation_id)
    bcr = bcr_crud.request_date_change(db, user, agreement, payload.proposed_start_date, reason=payload.reason)
    log_audit_event(db, None, "booking_change_request.submit", "booking_change_request", str(bcr.id), correlation_id, reason=f"user:{user.id}")
    db.commit()
    return bcr_crud.to_booking_change_request_read(bcr)


@router.post("/agreements/{agreement_id}/extension-requests", response_model=BookingChangeRequestRead, status_code=status.HTTP_201_CREATED)
def request_own_extension(
    agreement_id: int,
    payload: ExtensionRequestCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-008 Section 8 MVP: a renter requesting to extend their stay
    on an active occupancy -- the counterpart to change-requests above, which
    only covers a move-in date shift before move-in."""
    correlation_id = get_correlation_id(request)
    agreement = leasing_crud.get_agreement_or_404(db, agreement_id, correlation_id=correlation_id)
    bcr = bcr_crud.request_extension(db, user, agreement, payload.additional_term_months, reason=payload.reason)
    log_audit_event(db, None, "booking_change_request.submit", "booking_change_request", str(bcr.id), correlation_id, reason=f"user:{user.id}")
    db.commit()
    return bcr_crud.to_booking_change_request_read(bcr)


@router.post("/agreements/{agreement_id}/shortening-requests", response_model=BookingChangeRequestRead, status_code=status.HTTP_201_CREATED)
def request_own_shortening(
    agreement_id: int,
    payload: ShorteningRequestCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-008 Section 7.4/AC-10: only accepted before move-in -- once
    moved in, an early end routes to ending the tenancy (Section 6), not this
    endpoint (see crud/booking_change_requests.py:request_shortening)."""
    correlation_id = get_correlation_id(request)
    agreement = leasing_crud.get_agreement_or_404(db, agreement_id, correlation_id=correlation_id)
    bcr = bcr_crud.request_shortening(db, user, agreement, payload.reduced_term_months, reason=payload.reason)
    log_audit_event(db, None, "booking_change_request.submit", "booking_change_request", str(bcr.id), correlation_id, reason=f"user:{user.id}")
    db.commit()
    return bcr_crud.to_booking_change_request_read(bcr)


@router.post("/agreements/{agreement_id}/premises-change-requests", response_model=BookingChangeRequestRead, status_code=status.HTTP_201_CREATED)
def request_own_premises_change(
    agreement_id: int,
    payload: PremisesChangeRequestCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-008 Section 14: a renter asking to move to a different
    listing. Available before or after move-in -- see
    crud/booking_change_requests.py:request_premises_change."""
    correlation_id = get_correlation_id(request)
    agreement = leasing_crud.get_agreement_or_404(db, agreement_id, correlation_id=correlation_id)
    bcr = bcr_crud.request_premises_change(db, user, agreement, payload.target_listing_id, reason=payload.reason)
    log_audit_event(db, None, "booking_change_request.submit", "booking_change_request", str(bcr.id), correlation_id, reason=f"user:{user.id}")
    db.commit()
    return bcr_crud.to_booking_change_request_read(bcr)


@router.post("/agreements/{agreement_id}/financial-change-requests", response_model=BookingChangeRequestRead, status_code=status.HTTP_201_CREATED)
def request_own_financial_change(
    agreement_id: int,
    payload: FinancialChangeRequestCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-008 Section 10/AC-24: a renter requesting a new monthly
    rent. Available before or after move-in -- see
    crud/booking_change_requests.py:request_financial_change."""
    correlation_id = get_correlation_id(request)
    agreement = leasing_crud.get_agreement_or_404(db, agreement_id, correlation_id=correlation_id)
    bcr = bcr_crud.request_financial_change(db, user, agreement, payload.proposed_monthly_rent, reason=payload.reason)
    log_audit_event(db, None, "booking_change_request.submit", "booking_change_request", str(bcr.id), correlation_id, reason=f"user:{user.id}")
    db.commit()
    return bcr_crud.to_booking_change_request_read(bcr)


@router.get("/change-requests", response_model=list[BookingChangeRequestRead])
def list_own_change_requests(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return [bcr_crud.to_booking_change_request_read(b) for b in bcr_crud.list_change_requests_for_guest(db, user)]


@router.post("/change-requests/{bcr_id}/withdraw", response_model=BookingChangeRequestRead)
def withdraw_own_change_request(
    bcr_id: int, request: Request, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    correlation_id = get_correlation_id(request)
    bcr = bcr_crud.get_booking_change_request_or_404(db, bcr_id)
    updated = bcr_crud.withdraw_change_request(db, user, bcr)
    log_audit_event(db, None, "booking_change_request.withdraw", "booking_change_request", str(bcr_id), correlation_id, reason=f"user:{user.id}")
    db.commit()
    return bcr_crud.to_booking_change_request_read(updated)


@router.get("/agreements/{agreement_id}/disclosures", response_model=list[DisclosureRequirementRead])
def list_own_agreement_disclosures(
    agreement_id: int,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-004 Section 9.3/AC-15: the renter needs to see which
    disclosures exist and their status before they can acknowledge any of
    them -- previously only the admin-side list endpoint existed."""
    correlation_id = get_correlation_id(request)
    agreement = leasing_crud.get_agreement_or_404(db, agreement_id, correlation_id=correlation_id)
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")
    return agreement.disclosures


@router.post("/agreements/{agreement_id}/disclosures/{disclosure_id}/acknowledge", response_model=DisclosureRequirementRead)
def acknowledge_own_disclosure(
    agreement_id: int,
    disclosure_id: int,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-004 Section 9.3/AC-15: the renter's own confirmation of
    receipt for a disclosure the provider already delivered -- distinct from
    deliver_disclosure (which only records that Zoiko made it available)."""
    correlation_id = get_correlation_id(request)
    agreement = leasing_crud.get_agreement_or_404(db, agreement_id, correlation_id=correlation_id)
    disclosure = leasing_crud.get_disclosure_or_404(db, agreement, disclosure_id)
    updated = leasing_crud.user_acknowledge_disclosure(db, user, agreement, disclosure)
    log_audit_event(
        db, None, "user_disclosure.acknowledge", "disclosure_requirement", str(disclosure_id), correlation_id,
        reason=f"user:{user.id}", after_state=updated.status,
    )
    db.commit()
    return updated


@router.get("/agreements/{agreement_id}/pdf")
def download_own_agreement_pdf(
    agreement_id: int,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-004 Section 4.10: 'All contractual parties must have
    continuing access.' Previously only the admin route existed -- a renter
    had no way to download their own executed agreement at all."""
    correlation_id = get_correlation_id(request)
    agreement = leasing_crud.get_agreement_or_404(db, agreement_id, correlation_id=correlation_id)
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")

    log_audit_event(db, None, "user_agreement.download", "agreement", str(agreement_id), correlation_id, reason=f"user:{user.id}")
    db.commit()

    artifact = agreement.versions[-1].artifact if agreement.versions else None
    if artifact is not None:
        pdf_bytes = resolve_agreement_document_path(artifact.storage_ref).read_bytes()
    else:
        pdf_bytes = leasing_crud.generate_agreement_pdf(db, agreement)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="agreement-{agreement.id}.pdf"'},
    )


@router.get("/agreements/{agreement_id}/accessible-text")
def download_own_agreement_accessible_text(
    agreement_id: int,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-004 AC-28: the renter-facing screen-reader-friendly
    alternative to their own PDF above."""
    correlation_id = get_correlation_id(request)
    agreement = leasing_crud.get_agreement_or_404(db, agreement_id, correlation_id=correlation_id)
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")

    log_audit_event(
        db, None, "user_agreement.download", "agreement", str(agreement_id), correlation_id,
        reason=f"user:{user.id}:accessible_text",
    )
    db.commit()
    return Response(content=leasing_crud.generate_agreement_accessible_text(agreement), media_type="text/plain; charset=utf-8")


@router.get("/occupancies", response_model=list[UserOccupancyRead])
def list_user_occupancies(
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all active and past occupancies (rentals) for current user."""
    guest = get_guest_for_user(db, user)
    if not guest:
        return []

    occupancies = list(
        db.scalars(
            select(Occupancy)
            .where(Occupancy.guest_id == guest.id)
            .order_by(Occupancy.created_at.desc())
        )
    )

    return [_to_user_occupancy_read(db, occ) for occ in occupancies]


@router.get("/occupancies/{occupancy_id}", response_model=UserOccupancyRead)
def get_occupancy_details(
    occupancy_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get details of a specific occupancy/rental."""
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")

    # Verify ownership
    guest = get_guest_for_user(db, user)
    if not guest or occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view your own occupancies")

    return _to_user_occupancy_read(db, occupancy)


@router.post("/occupancies/{occupancy_id}/handover/receipt", response_model=HandoverEventRead)
def submit_handover_receipt(
    occupancy_id: int,
    payload: HandoverEventCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The renter alone records receipt; provider/admin cannot impersonate it."""
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")
    guest = get_guest_for_user(db, user)
    if not guest or occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only record receipt for your own occupancy")
    event = occupancy_crud.record_handover_event(
        db, occupancy, event_type="RENTER_RECEIPT", actor_kind="renter_user", actor_user_id=user.id,
        evidence_ref=payload.evidence_ref, notes=payload.notes, correlation_id=get_correlation_id(request),
    )
    log_audit_event(
        db, None, "occupancy.renter_receipt", "occupancy", str(occupancy_id),
        get_correlation_id(request), reason=f"user:{user.id}",
    )
    emit_event(db, "occupancy.renter_receipt_recorded", "occupancy", str(occupancy_id), {"handoverEventId": event.id})
    db.commit()
    return event


@router.get("/sublet-lookup", response_model=SubletRenterLookup)
def lookup_sublet_renter(email: str, db: Session = Depends(get_db)):
    """Resolve a proposed renter's email to a party for the sublet form, so the
    current tenant never has to ask them for a raw party ID. Only the minimum
    needed to confirm the right person was found is returned -- no other
    profile detail."""
    candidate = get_user_by_email(db, email.strip())
    if not candidate or not candidate.party_id:
        return SubletRenterLookup(found=False)
    verified = get_verified_identity_for_party(db, candidate.party_id) is not None
    return SubletRenterLookup(
        found=True,
        party_id=candidate.party_id,
        name=candidate.full_name,
        identity_verified=verified,
    )


@router.post("/occupancies/{occupancy_id}/sublet-request", response_model=SubletRequestRead, status_code=status.HTTP_201_CREATED)
def submit_sublet_request(
    occupancy_id: int,
    payload: SubletRequestCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Current renter requests to sublet their occupancy."""
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")
    if payload.occupancy_id != occupancy_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "occupancyId must match the requested occupancy")

    # Verify the user owns the occupancy
    guest = get_guest_for_user(db, user)
    if not guest or occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only sublet your own occupancies")

    # Verify proposed renter has identity verification
    if not sublet_crud.verify_sublet_identity(db, type("SubletRequest", (), {"proposed_renter_party_id": payload.proposed_renter_party_id})()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Proposed renter must have verified identity")

    sublet_request = sublet_crud.submit_sublet_request(
        db, user, occupancy_id, payload.proposed_renter_party_id, payload.arrangement_type,
        payload.authority_evidence_ref, payload.proposed_monthly_rent,
    )

    log_audit_event(db, None, "user_sublet_request.submit", "sublet_request", str(sublet_request.id), get_correlation_id(request), reason=f"user:{user.id}")
    emit_event(db, "sublet_request.submitted", "sublet_request", str(sublet_request.id), {"occupancyId": occupancy_id})
    db.commit()

    return sublet_crud.to_sublet_request_read(db, sublet_request)


@router.get("/sublet-requests", response_model=list[SubletRequestRead])
def list_user_sublet_requests(
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all sublet requests initiated by current user."""
    guest = get_guest_for_user(db, user)
    if not guest:
        return []

    sublet_requests = sublet_crud.list_sublet_requests_for_guest(db, guest.id)

    return [sublet_crud.to_sublet_request_read(db, sr) for sr in sublet_requests]


@router.get("/deposit-claims", response_model=list[DepositClaimRead])
def list_my_deposit_claims(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """Every deposit claim a host has submitted against any of this renter's
    occupancies, across every deposit they've ever funded."""
    guest = get_guest_for_user(db, user)
    if not guest:
        return []
    return [finance_crud.to_deposit_claim_read(c) for c in finance_crud.list_deposit_claims_for_guest(db, guest.id)]


@router.post("/deposit-claim-items/{item_id}/respond", response_model=DepositClaimItemRead)
def respond_to_deposit_claim_item(
    item_id: int,
    payload: DepositClaimItemRespond,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Renter accepts, partially accepts, or disputes one line item of a host's
    deposit claim. Ownership is enforced inside the crud layer -- it 404s/403s
    if this item's deposit doesn't trace back to the calling user's own guest
    record, the same pattern used for sublet-request ownership."""
    item = finance_crud.get_deposit_claim_item_or_404(db, item_id)
    updated = finance_crud.respond_to_deposit_claim_item(db, item, user, payload)
    log_audit_event(
        db, None, "user_deposit_claim_item.respond", "deposit_claim_item", str(item_id),
        get_correlation_id(request), reason=f"user:{user.id}:{payload.response}",
    )
    emit_event(db, "deposit_claim_item.responded", "deposit_claim_item", str(item_id), {"response": payload.response})
    db.commit()
    return finance_crud.to_deposit_claim_item_read(updated)


@router.get("/deposit-claim-items/{item_id}/evidence")
def get_my_deposit_claim_item_evidence(item_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """Lets a renter view the evidence behind a claim item before deciding
    whether to accept or dispute it -- without this, 'review the evidence' is
    just a phrase with nothing behind it."""
    item = finance_crud.get_deposit_claim_item_or_404(db, item_id)
    record = item.claim.deposit_record
    guest = get_guest_for_user(db, user)
    owner_guest_id = finance_crud.deposit_record_guest_id(record)
    if not guest or owner_guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This deposit claim does not belong to you")
    if not item.evidence_filename:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No evidence uploaded for this claim item")
    path = resolve_identity_document_path(item.evidence_filename)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence file is missing")
    return FileResponse(path, media_type=item.evidence_content_type, filename=item.evidence_original_name)


@router.post("/reviews", response_model=ReviewRead, status_code=status.HTTP_201_CREATED)
def submit_review(
    payload: ReviewCreate,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A renter may review a listing only once they've actually had an
    Occupancy there -- see review_crud.guest_has_stayed_at_listing. Rejects
    with 403 if they never rented it, 409 on a duplicate review, matching the
    existing ownership-check style used elsewhere in this router."""
    guest = get_guest_for_user(db, user)
    if not guest:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only review a listing you have rented")
    review = review_crud.create_review(db, guest.id, payload)
    return review_crud.to_review_read(review)
