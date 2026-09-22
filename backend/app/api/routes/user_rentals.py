import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.agreement_documents import resolve_agreement_document_path
from app.core.correlation import get_correlation_id
from app.core.identity_uploads import resolve_identity_document_path
from app.core.rate_limit import sublet_document_limiter, sublet_submit_limiter
from app.core.signed_urls import verify_signed_download_token
from app.crud import booking_change_requests as bcr_crud
from app.crud import sublet_documents as sublet_documents_crud
from app.crud import finance as finance_crud
from app.crud import leasing as leasing_crud
from app.crud import occupancy as occupancy_crud
from app.crud import occupancy_condition_report as condition_report_crud
from app.crud import habitability_incident as habitability_crud
from app.crud import host_entry_visit as host_entry_visit_crud
from app.crud import refund_entitlement as refund_entitlement_crud
from app.crud import review as review_crud
from app.crud import sublet as sublet_crud
from app.crud import termination as termination_crud
from app.crud.listing import assert_party_does_not_own_listing, resolve_market_release
from app.crud.rental_transaction_record import build_rental_transaction_record
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
from app.services.verification_requirements import is_identity_required_at_application
from app.models.user_account import UserAccount
from app.schemas.finance import DepositClaimItemRead, DepositClaimItemRespond, DepositClaimRead, PaymentPreviewRead
from app.schemas.habitability import HabitabilityIncidentCreate, HabitabilityIncidentRead
from app.schemas.host_entry_visit import HostEntryVisitRead
from app.schemas.leasing import (
    AgreementRead,
    BookingChangeRequestCreate,
    BookingChangeRequestRead,
    DepositChangeRequestCreate,
    DisclosureRequirementRead,
    ExtensionRequestCreate,
    FinancialChangeRequestCreate,
    PremisesChangeRequestCreate,
    ShorteningRequestCreate,
    OfferAcceptRequest,
    OfferRead,
    SubletRenterLookup,
    SubletRequestCreate,
    SubletRequestDecision,
    SubletRequestRead,
    SubletTerminologyRead,
    TermShiftRequestCreate,
    UserAgreementSignRequest,
    UserApplicationRead,
    UserApplicationSubmitRequest,
    UserOccupancyRead,
)
from app.schemas.activation_gate import HandoverEventCreate, HandoverEventRead
from app.schemas.occupancy import ConditionReportItemRead, PreMoveInCancellationRead, PreMoveInCancellationRequest
from app.schemas.rental_transaction_record import RentalTransactionRecordRead
from app.schemas.review import ReviewCreate, ReviewRead
from app.schemas.sublet_document import SubletDocumentRead
from app.schemas.termination import (
    RefundEntitlementRead,
    TerminationCaseCreate,
    TerminationCasePreviewRead,
    TerminationCasePreviewRequest,
    TerminationCaseRead,
)

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
        currency=listing.currency if listing else "USD",
        reassigned_via_sublet_request_id=occupancy_crud.get_reassigned_via_sublet_request_id(db, occupancy.id),
    )


@router.post("/applications", response_model=UserApplicationRead, status_code=status.HTTP_201_CREATED)
def submit_rental_application(
    payload: UserApplicationSubmitRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User submits a rental application for a listing.

    ZR-ENG-CLR-012 AC-03: identity verification is only required before
    application if the listing's own jurisdiction market pack explicitly
    opts into that earlier gate (identity_required_at_application) --
    progressive verification is the default; most listings need no
    identity at all until confirmation (see check_agreement_eligibility).
    """
    listing = db.get(Listing, payload.listing_id)

    jurisdiction_code = None
    if listing:
        market_release = resolve_market_release(db, listing)
        jurisdiction_code = market_release.jurisdiction if market_release else None
    if jurisdiction_code and is_identity_required_at_application(db, jurisdiction_code):
        if not user.party_id or not get_verified_identity_for_party(db, user.party_id):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "You must complete identity verification before submitting applications for this listing",
            )

    # A host cannot apply to their own listing -- enforced here regardless of
    # what the frontend shows, so it can't be bypassed by calling the API directly.
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


@router.post("/agreements/{agreement_id}/term-shift-requests", response_model=BookingChangeRequestRead, status_code=status.HTTP_201_CREATED)
def request_own_term_shift(
    agreement_id: int,
    payload: TermShiftRequestCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-008 Section 4: a renter requesting both a new move-in date
    and a new term length together, before they've moved in."""
    correlation_id = get_correlation_id(request)
    agreement = leasing_crud.get_agreement_or_404(db, agreement_id, correlation_id=correlation_id)
    bcr = bcr_crud.request_term_shift(
        db, user, agreement, payload.proposed_start_date, payload.new_term_months, reason=payload.reason,
    )
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
    bcr = bcr_crud.request_financial_change(
        db, user, agreement, payload.proposed_monthly_rent, reason=payload.reason,
        proposed_deposit_amount=payload.proposed_deposit_amount,
    )
    log_audit_event(db, None, "booking_change_request.submit", "booking_change_request", str(bcr.id), correlation_id, reason=f"user:{user.id}")
    db.commit()
    return bcr_crud.to_booking_change_request_read(bcr)


@router.post("/agreements/{agreement_id}/deposit-change-requests", response_model=BookingChangeRequestRead, status_code=status.HTTP_201_CREATED)
def request_own_deposit_change(
    agreement_id: int,
    payload: DepositChangeRequestCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-008 Section 4/11/DEPOSIT_CHANGE: a renter asking for a
    deposit change. See crud/booking_change_requests.py:request_deposit_change
    for why approving this never itself moves deposit money."""
    correlation_id = get_correlation_id(request)
    agreement = leasing_crud.get_agreement_or_404(db, agreement_id, correlation_id=correlation_id)
    bcr = bcr_crud.request_deposit_change(db, user, agreement, payload.proposed_deposit_amount, reason=payload.reason)
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


@router.post("/change-requests/{bcr_id}/accept-alternative", response_model=BookingChangeRequestRead)
def accept_own_alternative_change_terms(
    bcr_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-008 Section 18: renter's fresh consent to the host's
    counter-proposal (see crud/booking_change_requests.py:propose_alternative_terms)."""
    bcr = bcr_crud.get_booking_change_request_or_404(db, bcr_id)
    updated = bcr_crud.accept_alternative_terms(db, user, bcr)
    return bcr_crud.to_booking_change_request_read(updated)


@router.post("/change-requests/{bcr_id}/decline-alternative", response_model=BookingChangeRequestRead)
def decline_own_alternative_change_terms(
    bcr_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    bcr = bcr_crud.get_booking_change_request_or_404(db, bcr_id)
    updated = bcr_crud.decline_alternative_terms(db, user, bcr)
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


@router.get("/agreements/{agreement_id}/payment-preview", response_model=PaymentPreviewRead)
def get_own_payment_preview(
    agreement_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-005 AC-11: 'A payer sees complete amount-due-now line items
    and future schedule before charge confirmation' -- amount_due_now (rent +
    deposit, whatever's currently unpaid) and a projected future_schedule,
    read directly from this agreement's real Obligation/PaymentSchedule rows."""
    guest = get_guest_for_user(db, user)
    if not guest:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")
    return finance_crud.get_payment_preview_for_own_agreement(db, agreement_id, guest.id)


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


@router.get("/occupancies/{occupancy_id}/sublet-terminology", response_model=SubletTerminologyRead)
def get_own_sublet_terminology(
    occupancy_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-SUB-003 Section 8 sublet.uiTerm -- resolved before the create
    wizard renders, so it can show the jurisdiction-correct word."""
    from app.crud.market_policy import jurisdiction_code_for_occupancy, resolve_market_policy

    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")
    guest = get_guest_for_user(db, user)
    if not guest or occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view your own occupancies")
    policy = resolve_market_policy(db, jurisdiction_code_for_occupancy(occupancy))
    return SubletTerminologyRead(ui_term=policy.sublet_ui_term)


@router.post("/occupancies/{occupancy_id}/cancel-before-move-in", response_model=PreMoveInCancellationRead)
def cancel_own_booking_before_move_in(
    occupancy_id: int,
    request: Request,
    payload: PreMoveInCancellationRequest = PreMoveInCancellationRequest(),
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Section 7 gap: a renter cancelling their own signed-but-not-moved-in
    booking themselves, with a real refund -- previously the only thing
    that could end a PENDING_MOVE_IN occupancy was an admin action."""
    guest = get_guest_for_user(db, user)
    if not guest:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No guest record for this account")
    occupancy = occupancy_crud.get_occupancy_or_404(db, occupancy_id)
    correlation_id = get_correlation_id(request)
    updated, result = occupancy_crud.cancel_before_move_in(
        db, occupancy, guest=guest, reason=payload.reason, correlation_id=correlation_id,
    )
    log_audit_event(
        db, None, "occupancy.cancel_before_move_in", "occupancy", str(occupancy_id), correlation_id,
        reason=f"user:{user.id}; {payload.reason}",
    )
    emit_event(db, "occupancy.cancelled_before_move_in", "occupancy", str(occupancy_id), result)
    db.commit()
    return PreMoveInCancellationRead(occupancy=occupancy_crud.to_occupancy_read(db, updated), **result)


@router.get("/occupancies/{occupancy_id}/transaction-record", response_model=RentalTransactionRecordRead)
def get_own_rental_transaction_record(
    occupancy_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rental Transaction Record wireframe: a computed, read-only composite
    over this occupancy's own Application/Offer/Agreement, payments,
    handover/activation, sublet, and termination records -- see
    crud/rental_transaction_record.py:build_rental_transaction_record.
    Same ownership check as get_occupancy_details above. include_identity=True
    because this is the renter viewing their own identity claim."""
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")

    guest = get_guest_for_user(db, user)
    if not guest or occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view your own occupancies")

    return build_rental_transaction_record(db, occupancy, include_identity=True)


@router.post(
    "/occupancies/{occupancy_id}/termination-cases", response_model=TerminationCaseRead, status_code=status.HTTP_201_CREATED,
)
def request_own_termination(
    occupancy_id: int,
    payload: TerminationCaseCreate,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-006 Section 7.1: 'Renter selects End my stay / tenancy...
    submits notice/request.' Only models.termination_case.UNILATERAL_CAUSE_
    CODES are accepted today (see that module's own docstring for why)."""
    occupancy = occupancy_crud.get_occupancy_or_404(db, occupancy_id)
    guest = get_guest_for_user(db, user)
    if not guest:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This occupancy does not belong to you")
    return termination_crud.open_termination_case(db, occupancy, guest, payload)


@router.post("/occupancies/{occupancy_id}/termination-cases/preview", response_model=TerminationCasePreviewRead)
def preview_own_termination(
    occupancy_id: int,
    payload: TerminationCasePreviewRequest,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-006 Section 7.1 Step 5: shows the renter the resolved
    pathway, earliest effective date, and an estimated cost/refund range
    BEFORE they submit a real notice -- nothing here is persisted. See
    crud/termination.py:preview_termination_case."""
    occupancy = occupancy_crud.get_occupancy_or_404(db, occupancy_id)
    guest = get_guest_for_user(db, user)
    if not guest:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This occupancy does not belong to you")
    return termination_crud.preview_termination_case(db, occupancy, guest, payload)


@router.get("/occupancies/{occupancy_id}/termination-cases", response_model=list[TerminationCaseRead])
def list_own_termination_cases(
    occupancy_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    occupancy = occupancy_crud.get_occupancy_or_404(db, occupancy_id)
    guest = get_guest_for_user(db, user)
    if not guest or occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This occupancy does not belong to you")
    return termination_crud.list_termination_cases_for_occupancy(db, occupancy)


@router.post(
    "/occupancies/{occupancy_id}/habitability-incidents", response_model=HabitabilityIncidentRead, status_code=status.HTTP_201_CREATED,
)
def report_own_habitability_incident(
    occupancy_id: int,
    payload: HabitabilityIncidentCreate,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-006 Section 9: the renter's own 'report a problem' path.
    H2/H3 freezes the room for new bookings; never touches this occupancy."""
    occupancy = occupancy_crud.get_occupancy_or_404(db, occupancy_id)
    guest = get_guest_for_user(db, user)
    if not guest:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This occupancy does not belong to you")
    return habitability_crud.report_habitability_incident(db, occupancy, payload, guest=guest)


@router.get("/occupancies/{occupancy_id}/habitability-incidents", response_model=list[HabitabilityIncidentRead])
def list_own_habitability_incidents(
    occupancy_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    occupancy = occupancy_crud.get_occupancy_or_404(db, occupancy_id)
    guest = get_guest_for_user(db, user)
    if not guest or occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This occupancy does not belong to you")
    return habitability_crud.list_habitability_incidents_for_occupancy(db, occupancy)


@router.get("/occupancies/{occupancy_id}/entry-visits", response_model=list[HostEntryVisitRead])
def list_own_entry_visits(
    occupancy_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Section 9 gap: read-only transparency -- the renter can see upcoming
    and past Host entry visits for their own occupancy, never schedule one."""
    occupancy = occupancy_crud.get_occupancy_or_404(db, occupancy_id)
    guest = get_guest_for_user(db, user)
    if not guest or occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This occupancy does not belong to you")
    return host_entry_visit_crud.list_entry_visits_for_occupancy(db, occupancy)


@router.post("/termination-cases/{case_id}/withdraw", response_model=TerminationCaseRead)
def withdraw_own_termination_case(
    case_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    case = termination_crud.get_termination_case_or_404(db, case_id)
    guest = get_guest_for_user(db, user)
    if not guest:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This termination case does not belong to you")
    return termination_crud.withdraw_termination_case(db, case, guest)


@router.post("/termination-cases/{case_id}/accept-surrender", response_model=TerminationCaseRead)
def accept_own_mutual_surrender(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """ZR-ENG-CLR-006 Section 8.1 MUTUAL_SURRENDER_PROPOSAL: the renter's
    affirmative acceptance of a Host-proposed mutual surrender."""
    case = termination_crud.get_termination_case_or_404(db, case_id)
    guest = get_guest_for_user(db, user)
    if not guest:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This termination case does not belong to you")
    return termination_crud.accept_mutual_surrender(db, case, guest=guest)


@router.post("/termination-cases/{case_id}/decline-surrender", response_model=TerminationCaseRead)
def decline_own_mutual_surrender(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    case = termination_crud.get_termination_case_or_404(db, case_id)
    guest = get_guest_for_user(db, user)
    if not guest:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This termination case does not belong to you")
    return termination_crud.decline_mutual_surrender(db, case, guest=guest)


@router.get("/termination-cases/{case_id}/refund-entitlement", response_model=RefundEntitlementRead)
def get_own_latest_refund_entitlement(
    case_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-006 Section 17.1 case tracker: 'refund calculation' milestone
    -- the renter's own view of the latest itemized entitlement, whoever
    initiated the case. Ownership is checked against the occupancy (not the
    case's initiator), since a Host-initiated case still belongs to this
    renter's tenancy."""
    case = termination_crud.get_termination_case_or_404(db, case_id)
    guest = get_guest_for_user(db, user)
    if not guest or case.occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This termination case does not belong to you")
    return refund_entitlement_crud.get_latest_refund_entitlement_or_404(db, case)


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
    db.refresh(event)
    return event


@router.post("/occupancies/{occupancy_id}/move-out/notice", response_model=HandoverEventRead)
def submit_move_out_notice(
    occupancy_id: int,
    payload: HandoverEventCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Section 9 gap: the renter's own "I'm leaving on schedule" notice
    step for a naturally-expiring tenancy -- previously the only way an
    ACTIVE occupancy ended had zero renter-notice/host-verification
    handshake, unlike move-in's own 3-step handover. Mirrors
    submit_handover_receipt above -- the renter alone records their own
    notice; a Host/admin cannot impersonate it."""
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")
    guest = get_guest_for_user(db, user)
    if not guest or occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only give move-out notice for your own occupancy")
    event = occupancy_crud.record_handover_event(
        db, occupancy, event_type="MOVE_OUT_NOTICE_GIVEN", actor_kind="renter_user", actor_user_id=user.id,
        evidence_ref=payload.evidence_ref, notes=payload.notes, correlation_id=get_correlation_id(request),
    )
    log_audit_event(
        db, None, "occupancy.move_out_notice", "occupancy", str(occupancy_id),
        get_correlation_id(request), reason=f"user:{user.id}",
    )
    emit_event(db, "occupancy.move_out_notice_given", "occupancy", str(occupancy_id), {"handoverEventId": event.id})
    db.commit()
    db.refresh(event)
    return event


@router.post("/occupancies/{occupancy_id}/move-out/ready", response_model=HandoverEventRead)
def submit_move_out_ready(
    occupancy_id: int,
    payload: HandoverEventCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The renter confirming they've actually vacated -- the second leg of
    the move-out handshake, distinct from the earlier notice-of-intent."""
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")
    guest = get_guest_for_user(db, user)
    if not guest or occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only confirm move-out for your own occupancy")
    event = occupancy_crud.record_handover_event(
        db, occupancy, event_type="MOVE_OUT_READY", actor_kind="renter_user", actor_user_id=user.id,
        evidence_ref=payload.evidence_ref, notes=payload.notes, correlation_id=get_correlation_id(request),
    )
    log_audit_event(
        db, None, "occupancy.move_out_ready", "occupancy", str(occupancy_id),
        get_correlation_id(request), reason=f"user:{user.id}",
    )
    emit_event(db, "occupancy.move_out_ready", "occupancy", str(occupancy_id), {"handoverEventId": event.id})
    db.commit()
    return event


@router.post(
    "/occupancies/{occupancy_id}/condition-report", response_model=ConditionReportItemRead, status_code=status.HTTP_201_CREATED,
)
async def post_add_condition_report_item(
    occupancy_id: int,
    report_type: str = Form(...), area: str = Form(default=""), condition_rating: str | None = Form(default=None),
    notes: str = Form(default=""), file: UploadFile | None = File(default=None),
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Section 9 gap: the renter's own side of the move-in/move-out
    condition report -- previously there was no structured way for either
    party to document a room's condition with photos at all."""
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")
    guest = get_guest_for_user(db, user)
    if not guest or occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only add a condition report item for your own occupancy")
    item = await condition_report_crud.add_condition_report_item(
        db, occupancy, report_type=report_type, area=area, condition_rating=condition_rating, notes=notes,
        file=file, guest=guest,
    )
    log_audit_event(db, None, "occupancy.condition_report_item.add", "occupancy", str(occupancy_id), reason=f"user:{user.id}")
    db.commit()
    return condition_report_crud.to_condition_report_item_read(item)


@router.get("/occupancies/{occupancy_id}/condition-report", response_model=list[ConditionReportItemRead])
def get_own_condition_report(
    occupancy_id: int, report_type: str | None = None,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")
    guest = get_guest_for_user(db, user)
    if not guest or occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view your own occupancy's condition report")
    items = condition_report_crud.list_condition_report_items(db, occupancy, report_type=report_type)
    return [condition_report_crud.to_condition_report_item_read(i) for i in items]


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
    # ZR-SUB-003 Section 10: "Rate-limit submission... workflows."
    if not sublet_submit_limiter.allow(f"sublet_submit:user:{user.id}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many sublet requests submitted -- please wait before trying again.")
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
        payload.authority_evidence_ref, payload.proposed_monthly_rent, payload.reason,
        idempotency_key=payload.idempotency_key,
        proposed_start_date=payload.proposed_start_date, proposed_end_date=payload.proposed_end_date,
    )

    log_audit_event(db, None, "user_sublet_request.submit", "sublet_request", str(sublet_request.id), get_correlation_id(request), reason=f"user:{user.id}")
    emit_event(db, "sublet_request.submitted", "sublet_request", str(sublet_request.id), {"occupancyId": occupancy_id})
    db.commit()

    return sublet_crud.to_sublet_request_read(db, sublet_request)


@router.post("/occupancies/{occupancy_id}/sublet-request/draft", response_model=SubletRequestRead, status_code=status.HTTP_201_CREATED)
def create_sublet_request_draft(
    occupancy_id: int,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create an unsubmitted sublet-request draft for the tenant's own active occupancy."""
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")
    guest = get_guest_for_user(db, user)
    if not guest or occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only draft sublet requests for your own occupancies")

    draft = sublet_crud.create_draft_sublet_request(db, user, occupancy_id)
    log_audit_event(
        db, None, "user_sublet_request.draft_created", "sublet_request", str(draft.id),
        get_correlation_id(request), reason=f"user:{user.id}",
    )
    emit_event(db, "sublet_request.draft_created", "sublet_request", str(draft.id), {"occupancyId": occupancy_id})
    db.commit()
    return sublet_crud.to_sublet_request_read(db, draft)


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


@router.get("/sublet-requests/{sublet_request_id}/record")
def download_own_sublet_decision_record(
    sublet_request_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-SUB-003 Wireframe J: the tenant's own permanent, downloadable record
    of a completed sublet request."""
    guest = get_guest_for_user(db, user)
    sublet_request = sublet_crud.get_sublet_request(db, sublet_request_id)
    if not sublet_request:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sublet request not found")
    if not guest or sublet_request.requested_by_guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This sublet request does not belong to you")

    pdf_bytes = sublet_crud.generate_sublet_decision_record_pdf(db, sublet_request)
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="sublet-request-{sublet_request_id}.pdf"'},
    )


@router.post("/sublet-requests/{sublet_request_id}/respond", response_model=SubletRequestRead)
def respond_to_sublet_info_request(
    sublet_request_id: int,
    payload: SubletRequestDecision,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-SUB-003 Section 5.1: the tenant supplies the additional information
    the Host asked for, sending the request back to the Host's decision queue."""
    sublet_request = sublet_crud.get_sublet_request(db, sublet_request_id)
    if not sublet_request:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sublet request not found")
    updated = sublet_crud.respond_to_sublet_info_request(db, sublet_request, user, payload.notes)
    log_audit_event(
        db, None, "user_sublet_request.respond", "sublet_request", str(sublet_request_id),
        get_correlation_id(request), reason=f"user:{user.id}",
    )
    emit_event(db, "sublet_request.tenant_response_submitted", "sublet_request", str(sublet_request_id), {})
    db.commit()
    return sublet_crud.to_sublet_request_read(db, updated)


@router.post("/sublet-requests/{sublet_request_id}/withdraw", response_model=SubletRequestRead)
def withdraw_sublet_request(
    sublet_request_id: int,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-SUB-003: the tenant withdraws their own sublet request before a decision."""
    sublet_request = sublet_crud.get_sublet_request(db, sublet_request_id)
    if not sublet_request:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sublet request not found")
    updated = sublet_crud.withdraw_sublet_request(db, sublet_request, user)
    log_audit_event(
        db, None, "user_sublet_request.withdraw", "sublet_request", str(sublet_request_id),
        get_correlation_id(request), reason=f"user:{user.id}",
    )
    emit_event(db, "sublet_request.withdrawn", "sublet_request", str(sublet_request_id), {})
    db.commit()
    return sublet_crud.to_sublet_request_read(db, updated)


def _assert_tenant_owns_sublet_request(db: Session, sublet_request, user: UserAccount):
    guest = get_guest_for_user(db, user)
    if not guest or sublet_request.requested_by_guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This sublet request does not belong to you")
    return guest


@router.post(
    "/sublet-requests/{sublet_request_id}/documents", response_model=SubletDocumentRead, status_code=status.HTTP_201_CREATED,
)
async def upload_own_sublet_document(
    sublet_request_id: int, request: Request, file: UploadFile = File(...),
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-SUB-003 Section 3 Step 3/Section 10: the tenant's own document
    upload -- stored via the Evidence Vault (crud/sublet_documents.py),
    never claimed CLEAN (no live malware-scanning provider exists in this
    codebase)."""
    # ZR-SUB-003 Section 10: "Rate-limit... document workflows."
    if not sublet_document_limiter.allow(f"sublet_document_upload:user:{user.id}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many documents uploaded -- please wait before trying again.")
    sublet_request = sublet_crud.get_sublet_request(db, sublet_request_id)
    if not sublet_request:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sublet request not found")
    _assert_tenant_owns_sublet_request(db, sublet_request, user)
    document = await sublet_documents_crud.upload_sublet_document(db, sublet_request, file, uploaded_by_user_id=user.id)
    log_audit_event(
        db, None, "user_sublet_document.upload", "evidence_artifact", str(document.id),
        get_correlation_id(request), reason=f"user:{user.id}",
    )
    db.commit()
    return sublet_documents_crud.to_sublet_document_read(
        document, download_path=f"/api/users/rentals/sublet-requests/{sublet_request_id}/documents/{document.id}/file",
    )


@router.get("/sublet-requests/{sublet_request_id}/documents", response_model=list[SubletDocumentRead])
def list_own_sublet_documents(
    sublet_request_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    sublet_request = sublet_crud.get_sublet_request(db, sublet_request_id)
    if not sublet_request:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sublet request not found")
    _assert_tenant_owns_sublet_request(db, sublet_request, user)
    return [
        sublet_documents_crud.to_sublet_document_read(
            d, download_path=f"/api/users/rentals/sublet-requests/{sublet_request_id}/documents/{d.id}/file",
        )
        for d in sublet_documents_crud.list_sublet_documents(db, sublet_request)
    ]


@router.get("/sublet-requests/{sublet_request_id}/documents/{document_id}/file")
def download_own_sublet_document(
    sublet_request_id: int, document_id: int, token: str,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    sublet_request = sublet_crud.get_sublet_request(db, sublet_request_id)
    if not sublet_request:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sublet request not found")
    _assert_tenant_owns_sublet_request(db, sublet_request, user)
    document = sublet_documents_crud.get_sublet_document_or_404(db, sublet_request, document_id)
    verify_signed_download_token(token, "sublet_document", str(document.id))
    return sublet_documents_crud.sublet_document_file_response(document)


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
