from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.correlation import get_correlation_id
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
from app.crud.user import get_user_by_party_id
from app.db.session import get_db
from app.models.leasing import Application
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.user_account import UserAccount
from app.schemas.leasing import (
    AgreementRead,
    OfferRead,
    RequestMoveOut,
    SubletRequestCreate,
    SubletRequestRead,
    UserApplicationRead,
    UserApplicationSubmitRequest,
    UserOccupancyRead,
)
from app.schemas.review import ReviewCreate, ReviewRead

router = APIRouter(prefix="/api/users/rentals", tags=["user-rentals"], dependencies=[Depends(get_current_user)])


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
    since deleted, which the frontend renders gracefully."""
    listing = db.get(Listing, application.listing_id)
    property_address, property_city, host_name = _property_and_host(db, listing)
    latest_decision = max(application.decisions, key=lambda d: d.decided_at, default=None)
    return UserApplicationRead(
        id=application.id,
        listing_id=application.listing_id,
        listing_name=listing.name if listing else "",
        property_address=property_address,
        property_city=property_city,
        host_name=host_name,
        status=application.status,
        decision=latest_decision.decision if latest_decision else None,
        message=application.message,
        desired_move_in=application.desired_move_in,
        submitted_at=application.submitted_at,
        updated_at=application.updated_at,
    )


def _to_user_occupancy_read(db: Session, occupancy: Occupancy) -> UserOccupancyRead:
    listing = db.get(Listing, occupancy.listing_id)
    property_address, property_city, host_name = _property_and_host(db, listing)
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
        requested_move_out_date=occupancy.requested_move_out_date,
        move_out_requested_at=occupancy.move_out_requested_at,
        created_at=occupancy.created_at,
        ended_at=occupancy.ended_at,
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

    app_data = ApplicationCreate(listing_id=payload.listing_id, guest_id=guest.id, message=payload.message, desired_move_in=payload.desired_move_in)

    try:
        application = leasing_crud.submit_application(db, app_data)
        log_audit_event(db, None, "user_application.submit", "application", str(application.id), get_correlation_id(request), reason=f"user:{user.id}")

        listing = db.get(Listing, application.listing_id)
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
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


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


@router.get("/applications/{application_id}/agreement", response_model=AgreementRead | None)
def get_application_agreement(
    application_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Renter's own view of the agreement tied to their application, once the
    offer has progressed to an Agreement -- None until then."""
    application = db.get(Application, application_id)
    if not application:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")

    guest = get_guest_for_user(db, user)
    if not guest or application.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view your own application's agreement")

    if not application.offer:
        return None
    return application.offer.agreement


@router.get("/applications/{application_id}/offer", response_model=OfferRead | None)
def get_application_offer(
    application_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Renter's own view of the negotiated offer -- rent, deposit, lease term,
    start date -- so they can actually see what they're agreeing to before (and
    while) signing, instead of the agreement's bare sign/status state alone."""
    application = db.get(Application, application_id)
    if not application:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")

    guest = get_guest_for_user(db, user)
    if not guest or application.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view your own application's offer")

    return application.offer


@router.get("/applications/{application_id}/agreement/pdf")
def download_application_agreement_pdf(
    application_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Renter's own copy of the agreement PDF -- same generator the admin side
    uses, ownership-checked via the renter's own guest_id."""
    application = db.get(Application, application_id)
    if not application:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")

    guest = get_guest_for_user(db, user)
    if not guest or application.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only download your own agreement")

    if not application.offer or not application.offer.agreement:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No agreement to download yet")

    pdf_bytes = leasing_crud.generate_agreement_pdf(application.offer.agreement)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="agreement-{application.offer.agreement.id}.pdf"'},
    )


@router.post("/applications/{application_id}/agreement/sign", response_model=AgreementRead)
def sign_application_agreement(
    application_id: int,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Renter's own simulated e-signature on their agreement -- same mechanics
    as the admin-attested signature (leasing_crud.sign_agreement), but
    self-authorized by the renter owning the application/offer/guest chain."""
    application = db.get(Application, application_id)
    if not application:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")

    guest = get_guest_for_user(db, user)
    if not guest or application.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only sign your own agreement")

    if not application.offer or not application.offer.agreement:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No agreement to sign yet")

    agreement = leasing_crud.sign_agreement_as_renter(db, application.offer.agreement, guest)
    log_audit_event(
        db, None, "user_agreement.sign", "agreement", str(agreement.id), get_correlation_id(request), reason=f"user:{user.id}"
    )
    if agreement.status == "SIGNED":
        emit_event(db, "agreement.signed", "agreement", str(agreement.id), {})
    db.commit()

    return agreement


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


@router.post("/occupancies/{occupancy_id}/request-move-out", response_model=UserOccupancyRead)
def request_occupancy_move_out(
    occupancy_id: int,
    payload: RequestMoveOut,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Renter's own notice of intent to vacate -- previously there was no
    self-service way to signal this at all; ending the occupancy itself stays
    an admin action (deposit inspection/release happens through Finance)."""
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")

    guest = get_guest_for_user(db, user)
    if not guest or occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only request move-out for your own occupancy")

    updated = occupancy_crud.request_move_out(db, occupancy, payload.desired_move_out_date)
    log_audit_event(
        db, None, "user_occupancy.request_move_out", "occupancy", str(occupancy_id),
        get_correlation_id(request), reason=f"user:{user.id}",
    )
    db.commit()

    return _to_user_occupancy_read(db, updated)


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
        db, user, occupancy_id, payload.proposed_renter_party_id, payload.authority_evidence_ref
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
    from app.models.sublet_request import SubletRequest

    guest = get_guest_for_user(db, user)
    if not guest:
        return []

    sublet_requests = list(
        db.scalars(
            select(SubletRequest)
            .join(Occupancy, Occupancy.id == SubletRequest.current_occupancy_id)
            .where(Occupancy.guest_id == guest.id)
            .order_by(SubletRequest.created_at.desc())
        )
    )

    return [sublet_crud.to_sublet_request_read(db, sr) for sr in sublet_requests]


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
