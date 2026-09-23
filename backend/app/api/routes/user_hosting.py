from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.correlation import get_correlation_id
from app.core.image_uploads import save_listing_images
from app.core.rate_limit import sublet_document_limiter
from app.core.signed_urls import verify_signed_download_token
from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.crud import authority as authority_crud
from app.crud import leasing as leasing_crud
from app.crud import listing as listing_crud
from app.crud import occupancy as occupancy_crud
from app.crud import payment_connection as payment_connection_crud
from app.crud import payment_recipient_authority as payment_recipient_authority_crud
from app.crud import property_verification as property_verification_crud
from app.crud import sublet as sublet_crud
from app.crud import sublet_documents as sublet_documents_crud
from app.crud.property import get_property, get_room, list_rooms_for_property
from app.crud.rental_transaction_record import build_rental_transaction_record
from app.db.session import get_db
from app.models.occupancy import Occupancy
from app.models.user_account import UserAccount
from app.schemas.occupancy import PreMoveInCancellationRead, PreMoveInCancellationRequest
from app.schemas.sublet_document import SubletDocumentRead
from app.schemas.leasing import (
    AgreementCreateRequest,
    AgreementRead,
    ApplicationDecide,
    ApplicationRead,
    DisclosureDeliverRequest,
    DisclosureRequirementRead,
    OfferRead,
    OfferTermsCreate,
    OfferTermsRead,
    SubletChronologyEvent,
    SubletRequestDecision,
    SubletRequestRead,
    UserAgreementSignRequest,
)
from app.schemas.marketplace import AuthorityRecordDeclare, AuthorityRecordRead, PropertyCreate, PropertyRead, RoomCreate, RoomRead
from app.schemas.listing import ListingCreate, ListingRead, ListingUpdate
from app.schemas.occupancy import OccupancyRead
from app.schemas.rental_transaction_record import RentalTransactionRecordRead
from app.schemas.verification import PropertyVerificationDeclare, PropertyVerificationRead
from app.schemas.payment_connection import PaymentConnectionRead
from app.models.payment_recipient_authority import PaymentRecipientAuthority
from app.schemas.payment_recipient_authority import (
    PaymentRecipientAuthorityConfirmChange,
    PaymentRecipientAuthorityDeclare,
    PaymentRecipientAuthorityRead,
)

router = APIRouter(prefix="/api/users/hosting", tags=["user-hosting"], dependencies=[Depends(get_current_user)])


@router.post("/uploads/images")
async def upload_user_listing_images(
    files: list[UploadFile],
    user: UserAccount = Depends(get_current_user),
):
    """Lets a host upload photos for their own listings. USER-authenticated
    (get_current_user) -- deliberately never get_current_admin, and shares
    validation/storage with the admin upload endpoint via save_listing_images
    rather than duplicating it. Stores into the same PUBLIC upload_dir as the
    admin path; identity documents never pass through here."""
    urls = await save_listing_images(files)
    return {"urls": urls}


def _get_property_or_404(db: Session, property_id: int, user: UserAccount):
    """Get property and verify ownership."""
    prop = get_property(db, property_id)
    if not prop:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Property not found")
    if not user.party_id or prop.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only manage your own properties")
    return prop


@router.post("/occupancies/{occupancy_id}/cancel-before-move-in", response_model=PreMoveInCancellationRead)
def cancel_hosted_booking_before_move_in(
    occupancy_id: int,
    request: Request,
    payload: PreMoveInCancellationRequest = PreMoveInCancellationRequest(),
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Section 7 gap: a Host cancelling a signed-but-not-moved-in booking on
    their own property -- previously there was no Host-initiated pre-move-in
    cancellation path at all (open_host_termination_case requires ACTIVE)."""
    if not user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No host party on this account")
    occupancy = occupancy_crud.get_occupancy_or_404(db, occupancy_id)
    correlation_id = get_correlation_id(request)
    updated, result = occupancy_crud.cancel_before_move_in(
        db, occupancy, host_party_id=user.party_id, reason=payload.reason, correlation_id=correlation_id,
    )
    log_audit_event(
        db, None, "occupancy.cancel_before_move_in", "occupancy", str(occupancy_id), correlation_id,
        reason=f"host_user:{user.id}; {payload.reason}",
    )
    emit_event(db, "occupancy.cancelled_before_move_in", "occupancy", str(occupancy_id), result)
    db.commit()
    return PreMoveInCancellationRead(occupancy=occupancy_crud.to_occupancy_read(db, updated), **result)


@router.get("/properties", response_model=list[PropertyRead])
def list_properties(
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all properties owned by current user."""
    if not user.party_id:
        return []
    
    from sqlalchemy import select
    from app.models.property import Property

    properties = list(
        db.scalars(select(Property).where(Property.owner_party_id == user.party_id))
    )
    return properties


@router.post("/properties", response_model=PropertyRead, status_code=status.HTTP_201_CREATED)
def create_user_property(
    payload: PropertyCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new property as a host."""
    if not user.party_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no associated party")
    
    from app.models.property import Property

    prop = Property(
        owner_party_id=user.party_id,
        address=payload.address,
        city=payload.city,
        status="active",
        jurisdiction_code=payload.jurisdiction_code,
    )
    db.add(prop)
    db.commit()
    db.refresh(prop)

    log_audit_event(db, None, "user_property.create", "property", str(prop.id), get_correlation_id(request), reason=f"user:{user.id}")
    db.commit()
    return prop


@router.put("/properties/{property_id}", response_model=PropertyRead)
def update_user_property(
    property_id: int,
    payload: PropertyCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a property."""
    prop = _get_property_or_404(db, property_id, user)
    
    prop.address = payload.address
    prop.city = payload.city
    prop.jurisdiction_code = payload.jurisdiction_code
    db.commit()
    db.refresh(prop)

    log_audit_event(db, None, "user_property.update", "property", str(property_id), get_correlation_id(request), reason=f"user:{user.id}")
    db.commit()
    return prop


@router.get("/properties/{property_id}/rooms", response_model=list[RoomRead])
def list_property_rooms(
    property_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all rooms in a property."""
    prop = _get_property_or_404(db, property_id, user)
    return list_rooms_for_property(db, property_id)


@router.post("/properties/{property_id}/rooms", response_model=RoomRead, status_code=status.HTTP_201_CREATED)
def create_user_room(
    property_id: int,
    payload: RoomCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add a room to a property."""
    prop = _get_property_or_404(db, property_id, user)
    
    from app.models.room import Room

    room = Room(
        property_id=prop.id,
        room_type="private_room",
        size=payload.size,
        has_ensuite=payload.has_ensuite,
        status="active",
    )
    db.add(room)
    db.commit()
    db.refresh(room)

    log_audit_event(db, None, "user_room.create", "room", str(room.id), get_correlation_id(request), reason=f"user:{user.id}")
    db.commit()
    return room


@router.put("/properties/{property_id}/rooms/{room_id}", response_model=RoomRead)
def update_user_room(
    property_id: int,
    room_id: int,
    payload: RoomCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a room in a property."""
    prop = _get_property_or_404(db, property_id, user)
    
    from app.models.room import Room

    room = db.get(Room, room_id)
    if not room or room.property_id != prop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")

    room.size = payload.size
    room.has_ensuite = payload.has_ensuite
    db.commit()
    db.refresh(room)

    log_audit_event(db, None, "user_room.update", "room", str(room_id), get_correlation_id(request), reason=f"user:{user.id}")
    db.commit()
    return room


def _get_user_listing_or_404(db: Session, listing_id: str, user: UserAccount):
    listing = listing_crud.get_listing(db, listing_id)
    if not listing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")
    if not user.party_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no associated party")
    listing_crud.assert_party_owns_listing(listing, user.party_id)
    return listing


@router.get("/listings", response_model=list[ListingRead])
def list_user_listings(
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List every listing (draft or published) owned by the current user's party.
    Backs the "My Listings" page -- previously there was no way to read a host's
    own drafts back, so the frontend cached write results in localStorage as a
    stopgap. That stopgap is no longer needed now that this exists."""
    if not user.party_id:
        return []
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload
    from app.models.listing import Listing

    listings = list(
        db.scalars(
            select(Listing)
            .options(joinedload(Listing.room))
            .where(Listing.party_id == user.party_id)
            .order_by(Listing.id.desc())
        )
    )
    return listing_crud.annotate_availability(db, listings)


@router.post("/listings", response_model=ListingRead, status_code=status.HTTP_201_CREATED)
def create_user_listing(
    payload: ListingCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.party_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no associated party")
    listing = listing_crud.create_listing_for_party(db, payload, user.party_id)
    log_audit_event(db, None, "user_listing.create", "listing", listing.id, get_correlation_id(request), reason=f"user:{user.id}")
    db.commit()
    return listing


@router.put("/listings/{listing_id}", response_model=ListingRead)
def update_user_listing(
    listing_id: str,
    payload: ListingUpdate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    listing = _get_user_listing_or_404(db, listing_id, user)
    if payload.room_id is not None:
        listing_crud.assert_party_owns_room(db, payload.room_id, user.party_id)
    updated = listing_crud.update_listing(db, listing, payload)
    log_audit_event(db, None, "user_listing.update", "listing", listing_id, get_correlation_id(request), reason=f"user:{user.id}")
    db.commit()
    return updated


@router.get("/listings/{listing_id}/publish-eligibility")
def get_user_listing_publish_eligibility(
    listing_id: str,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    listing = _get_user_listing_or_404(db, listing_id, user)
    reasons = listing_crud.check_publish_eligibility(db, listing)
    return {"eligible": not reasons, "reasons": reasons}


@router.post("/listings/{listing_id}/submit-for-review", response_model=ListingRead)
def submit_user_listing_for_review(
    listing_id: str,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A host can only ask for review -- publishing is always an explicit
    admin/super-admin decision (see listings.py's publish/reject endpoints)."""
    listing = _get_user_listing_or_404(db, listing_id, user)
    updated = listing_crud.submit_listing_for_review(db, listing)
    log_audit_event(
        db, None, "user_listing.submit_for_review", "listing", listing_id, get_correlation_id(request),
        reason=f"user:{user.id}",
    )
    emit_event(db, "listing.submitted", "listing", listing_id, {"room_id": updated.room_id, "partyId": user.party_id})
    db.commit()
    return updated


# --- Applications (ZR-ENG-CLR-011 Section 10: Host "Applications to review") ----


@router.get("/applications", response_model=list[ApplicationRead])
def list_hosted_applications(
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Applications submitted to any of the host's own party-owned listings."""
    applications = leasing_crud.list_applications_for_host(db, user)
    return [leasing_crud.to_application_read(a) for a in applications]


@router.get("/applications/{application_id}", response_model=ApplicationRead)
def get_hosted_application(
    application_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    application = leasing_crud.get_application_for_host_or_404(db, application_id, user)
    return leasing_crud.to_application_read(application)


@router.post("/applications/{application_id}/decide", response_model=ApplicationRead)
def decide_hosted_application(
    application_id: int,
    payload: ApplicationDecide,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    application = leasing_crud.get_application_for_host_or_404(db, application_id, user)
    leasing_crud.decide_application_as_host(db, application, user, payload)
    log_audit_event(
        db, None, "user_application.decide", "application", str(application_id), get_correlation_id(request),
        reason=f"user:{user.id}:{payload.decision}",
    )
    emit_event(db, "application.decided", "application", str(application_id), {"decision": payload.decision})
    db.commit()
    db.refresh(application)
    return leasing_crud.to_application_read(application)


# --- Offers and agreements (ZR-ENG-CLR-004 Section 4.3: "The default is the
# legal landlord/Host... Zoiko Admin does not sign merely because Zoiko
# operates the platform.") ------------------------------------------------


@router.post("/applications/{application_id}/offers", response_model=OfferRead, status_code=status.HTTP_201_CREATED)
def create_hosted_offer(
    application_id: int,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    application = leasing_crud.get_application_for_host_or_404(db, application_id, user)
    offer = leasing_crud.create_offer(db, application, user)
    log_audit_event(
        db, None, "user_offer.create", "offer", str(offer.id), get_correlation_id(request), reason=f"user:{user.id}",
    )
    emit_event(db, "offer.created", "offer", str(offer.id), {"applicationId": application_id})
    db.commit()
    return offer


@router.get("/offers/{offer_id}", response_model=OfferRead)
def get_hosted_offer(offer_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return leasing_crud.get_offer_for_host_or_404(db, offer_id, user)


@router.post("/offers/{offer_id}/terms", response_model=OfferTermsRead)
def create_hosted_offer_terms(
    offer_id: int,
    payload: OfferTermsCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    correlation_id = get_correlation_id(request)
    offer = leasing_crud.get_offer_for_host_or_404(db, offer_id, user)
    terms = leasing_crud.add_offer_terms(db, offer, user, payload, correlation_id=correlation_id)
    log_audit_event(db, None, "user_offer.add_terms", "offer", str(offer_id), correlation_id, reason=f"user:{user.id}")
    emit_event(
        db, "offer.terms_added", "offer", str(offer_id), {"version": terms.version, "monthlyRent": float(terms.monthly_rent)},
    )
    db.commit()
    return terms


@router.post("/offers/{offer_id}/send", response_model=OfferRead)
def send_hosted_offer(
    offer_id: int, request: Request, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    correlation_id = get_correlation_id(request)
    offer = leasing_crud.get_offer_for_host_or_404(db, offer_id, user)
    updated = leasing_crud.set_offer_status(db, offer, user, "SENT", correlation_id=correlation_id)
    log_audit_event(db, None, "user_offer.send", "offer", str(offer_id), correlation_id, reason=f"user:{user.id}")
    db.commit()
    return updated


@router.post("/offers/{offer_id}/agreement", response_model=AgreementRead, status_code=status.HTTP_201_CREATED)
def create_hosted_agreement(
    offer_id: int,
    request: Request,
    payload: AgreementCreateRequest = AgreementCreateRequest(),
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    correlation_id = get_correlation_id(request)
    offer = leasing_crud.get_offer_for_host_or_404(db, offer_id, user)
    agreement = leasing_crud.create_agreement(
        db, offer, user, payload.selected_optional_clause_ids,
        signing_as_agent=payload.signing_as_agent,
        agent_authority_evidence_ref=payload.agent_authority_evidence_ref,
    )
    log_audit_event(
        db, None, "user_agreement.create", "agreement", str(agreement.id), correlation_id, reason=f"user:{user.id}",
    )
    emit_event(db, "agreement.created", "agreement", str(agreement.id), {"offerId": offer_id})
    db.commit()
    return agreement


@router.get("/agreements/{agreement_id}", response_model=AgreementRead)
def get_hosted_agreement(
    agreement_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    return leasing_crud.get_agreement_for_host_or_404(db, agreement_id, user)


@router.get("/agreements/{agreement_id}/disclosures", response_model=list[DisclosureRequirementRead])
def list_hosted_agreement_disclosures(
    agreement_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    agreement = leasing_crud.get_agreement_for_host_or_404(db, agreement_id, user)
    return agreement.disclosures


@router.post(
    "/agreements/{agreement_id}/disclosures/{disclosure_id}/deliver", response_model=DisclosureRequirementRead,
)
def deliver_hosted_agreement_disclosure(
    agreement_id: int,
    disclosure_id: int,
    request: Request,
    payload: DisclosureDeliverRequest = DisclosureDeliverRequest(),
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agreement = leasing_crud.get_agreement_for_host_or_404(db, agreement_id, user)
    disclosure = leasing_crud.get_disclosure_or_404(db, agreement, disclosure_id)
    updated = leasing_crud.deliver_disclosure(
        db, agreement, disclosure, user, to_party=payload.to_party, delivery_channel=payload.delivery_channel,
    )
    log_audit_event(
        db, None, "user_disclosure.deliver", "disclosure_requirement", str(disclosure_id), get_correlation_id(request),
        reason=f"user:{user.id}", after_state=updated.status,
    )
    db.commit()
    return updated


@router.post("/agreements/{agreement_id}/send", response_model=AgreementRead)
def send_hosted_agreement(
    agreement_id: int, request: Request, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    agreement = leasing_crud.get_agreement_for_host_or_404(db, agreement_id, user)
    updated = leasing_crud.send_agreement(db, agreement, user)
    log_audit_event(
        db, None, "user_agreement.send", "agreement", str(agreement_id), get_correlation_id(request), reason=f"user:{user.id}",
    )
    db.commit()
    return updated


@router.post("/agreements/{agreement_id}/sign", response_model=AgreementRead)
def sign_hosted_agreement(
    agreement_id: int,
    request: Request,
    payload: UserAgreementSignRequest = UserAgreementSignRequest(),
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    correlation_id = get_correlation_id(request)
    agreement = leasing_crud.get_agreement_for_host_or_404(db, agreement_id, user)
    updated = leasing_crud.host_sign_agreement(db, agreement, user, method=payload.method, evidence_metadata=payload.evidence_metadata)
    log_audit_event(
        db, None, "user_agreement.sign", "agreement", str(agreement_id), correlation_id, reason=f"user:{user.id}:provider",
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


# --- Sublet requests (ZR-SUB-003: 'A tenant's request for permission to sublet
# must be sent to the verified landlord, agent or other authorized property
# representative. Zoiko Rooms records and routes the request; it does not
# grant permission on the owner's behalf.' -- the Host, not Zoiko Admin, is
# the real decision-maker.) -------------------------------------------------


@router.get("/sublet-requests", response_model=list[SubletRequestRead])
def list_hosted_sublet_requests(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    requests = sublet_crud.list_sublet_requests_for_host(db, user)
    return [sublet_crud.to_sublet_request_read(db, r) for r in requests]


@router.get("/sublet-requests/{sublet_request_id}", response_model=SubletRequestRead)
def get_hosted_sublet_request(
    sublet_request_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    sublet_request = sublet_crud.get_sublet_request_for_host_or_404(db, sublet_request_id, user)
    return sublet_crud.to_sublet_request_read(db, sublet_request)


@router.get("/sublet-requests/{sublet_request_id}/record")
def download_hosted_sublet_decision_record(
    sublet_request_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-SUB-003 Wireframe J: 'The provider sees the same canonical decision
    facts.' The Host's own copy of the downloadable record."""
    sublet_request = sublet_crud.get_sublet_request_for_host_or_404(db, sublet_request_id, user)
    pdf_bytes = sublet_crud.generate_sublet_decision_record_pdf(db, sublet_request)
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="sublet-request-{sublet_request_id}.pdf"'},
    )


@router.post("/sublet-requests/{sublet_request_id}/request-info", response_model=SubletRequestRead)
def request_hosted_sublet_more_info(
    sublet_request_id: int,
    request: Request,
    payload: SubletRequestDecision,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The Host asks the tenant for more information before deciding."""
    sublet_request = sublet_crud.get_sublet_request_for_host_or_404(db, sublet_request_id, user)
    updated = sublet_crud.request_more_sublet_info(
        db, sublet_request, user, payload.notes,
        requested_document_types=payload.requested_document_types, due_at=payload.due_at,
    )
    log_audit_event(
        db, None, "user_sublet_request.request_info", "sublet_request", str(sublet_request_id),
        get_correlation_id(request), reason=f"user:{user.id}",
    )
    emit_event(db, "sublet_request.more_information_requested", "sublet_request", str(sublet_request_id), {})
    db.commit()
    return sublet_crud.to_sublet_request_read(db, updated)


@router.post("/sublet-requests/{sublet_request_id}/approve", response_model=SubletRequestRead)
def approve_hosted_sublet_request(
    sublet_request_id: int,
    request: Request,
    payload: SubletRequestDecision | None = None,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The Host approves a sublet request for their own listing."""
    sublet_request = sublet_crud.get_sublet_request_for_host_or_404(db, sublet_request_id, user)
    approved = sublet_crud.approve_sublet_request(
        db, sublet_request, user,
        payload.notes if payload else "", payload.conditions if payload else "", payload.expires_at if payload else None,
        condition_list=payload.condition_list if payload else None,
        authority_confirmed=payload.authority_confirmed if payload else False,
        step_up_password=payload.step_up_password if payload else "",
    )
    log_audit_event(
        db, None, "user_sublet_request.approve", "sublet_request", str(sublet_request_id),
        get_correlation_id(request), reason=f"user:{user.id}",
    )
    emit_event(
        db, "sublet_request.approved", "sublet_request", str(sublet_request_id),
        {"occupancyId": approved.current_occupancy_id, "arrangementType": approved.arrangement_type},
    )
    db.commit()
    db.refresh(approved)
    return sublet_crud.to_sublet_request_read(db, approved)


@router.post("/sublet-requests/{sublet_request_id}/decline", response_model=SubletRequestRead)
def decline_hosted_sublet_request(
    sublet_request_id: int,
    request: Request,
    payload: SubletRequestDecision | None = None,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The Host declines a sublet request for their own listing."""
    sublet_request = sublet_crud.get_sublet_request_for_host_or_404(db, sublet_request_id, user)
    declined = sublet_crud.reject_sublet_request(
        db, sublet_request, user, payload.notes if payload else "", payload.decline_reason_code if payload else "",
    )
    log_audit_event(
        db, None, "user_sublet_request.decline", "sublet_request", str(sublet_request_id),
        get_correlation_id(request), reason=f"user:{user.id}",
    )
    emit_event(db, "sublet_request.rejected", "sublet_request", str(sublet_request_id), {"occupancyId": declined.current_occupancy_id})
    db.commit()
    db.refresh(declined)
    return sublet_crud.to_sublet_request_read(db, declined)


@router.get("/sublet-requests/{sublet_request_id}/audit", response_model=list[SubletChronologyEvent])
def get_hosted_sublet_request_audit_trail(
    sublet_request_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-SUB-003 Section 12/Wireframe J: 'The provider sees the same
    canonical decision facts.' The Host's own privileged audit view."""
    sublet_request = sublet_crud.get_sublet_request_for_host_or_404(db, sublet_request_id, user)
    return sublet_crud.build_sublet_audit_trail(sublet_request)


@router.post(
    "/sublet-requests/{sublet_request_id}/documents", response_model=SubletDocumentRead, status_code=status.HTTP_201_CREATED,
)
async def upload_hosted_sublet_document(
    sublet_request_id: int, request: Request, file: UploadFile = File(...),
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """The Host's own document upload (e.g. a landlord consent letter) on a
    sublet request for their own listing."""
    if not sublet_document_limiter.allow(f"sublet_document_upload:user:{user.id}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many documents uploaded -- please wait before trying again.")
    sublet_request = sublet_crud.get_sublet_request_for_host_or_404(db, sublet_request_id, user)
    document = await sublet_documents_crud.upload_sublet_document(db, sublet_request, file, uploaded_by_user_id=user.id)
    log_audit_event(
        db, None, "user_sublet_document.upload", "evidence_artifact", str(document.id),
        get_correlation_id(request), reason=f"user:{user.id}",
    )
    db.commit()
    return sublet_documents_crud.to_sublet_document_read(
        document, download_path=f"/api/users/hosting/sublet-requests/{sublet_request_id}/documents/{document.id}/file",
    )


@router.get("/sublet-requests/{sublet_request_id}/documents", response_model=list[SubletDocumentRead])
def list_hosted_sublet_documents(
    sublet_request_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    sublet_request = sublet_crud.get_sublet_request_for_host_or_404(db, sublet_request_id, user)
    return [
        sublet_documents_crud.to_sublet_document_read(
            d, download_path=f"/api/users/hosting/sublet-requests/{sublet_request_id}/documents/{d.id}/file",
        )
        for d in sublet_documents_crud.list_sublet_documents(db, sublet_request)
    ]


@router.get("/sublet-requests/{sublet_request_id}/documents/{document_id}/file")
def download_hosted_sublet_document(
    sublet_request_id: int, document_id: int, token: str,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    sublet_request = sublet_crud.get_sublet_request_for_host_or_404(db, sublet_request_id, user)
    document = sublet_documents_crud.get_sublet_document_or_404(db, sublet_request, document_id)
    verify_signed_download_token(token, "sublet_document", str(document.id))
    return sublet_documents_crud.sublet_document_file_response(document)


# --- Lister, Property & Authority Verification: Host self-service submission ---
# Deliberately separate from IdentityVerification (who the lister is). Both
# routes below scope themselves to a room the calling host's own party
# actually owns via get_room + the crud layer's own ownership check --
# same shape as _get_property_or_404 above, just at the room level.


@router.get("/rooms/{room_id}/authority-records", response_model=list[AuthorityRecordRead])
def list_hosted_room_authority_records(
    room_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    room = get_room(db, room_id)
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    return authority_crud.list_authority_records_for_room_owned_by(db, user, room)


@router.post(
    "/rooms/{room_id}/authority-records", response_model=AuthorityRecordRead, status_code=status.HTTP_201_CREATED,
)
def declare_hosted_authority_record(
    room_id: int,
    payload: AuthorityRecordDeclare,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.room_id != room_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "roomId in the body must match the room in the URL")
    room = get_room(db, room_id)
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    record = authority_crud.declare_authority_record(
        db, user, room, relationship_type=payload.relationship_type, evidence_ref=payload.evidence_ref,
    )
    emit_event(
        db, "authority_record.declared", "authority_record", str(record.id),
        {"roomId": room_id}, correlation_id=get_correlation_id(request),
    )
    db.commit()
    return record


# --- ZR-PAY-LINK-003 Section 1.1/2: payment-receipt authority, deliberately
# separate from the list-authority routes above -- "authority to list" and
# "authority to receive payments" are separate claims. Unlike the routes
# above, recipient_party_id may name a party other than the caller.


@router.get("/rooms/{room_id}/payment-connection", response_model=PaymentConnectionRead)
def get_hosted_room_payment_connection(room_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """ZR-PAY-LINK-003 Section 3.1 -- the consolidated recipient+destination
    status view, ahead of the raw authority-list route below so a host can
    see *why* payments aren't ACTIVE yet without cross-referencing two
    endpoints."""
    room = get_room(db, room_id)
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    return payment_connection_crud.get_payment_connection_for_room_owned_by(db, user, room)


@router.get("/rooms/{room_id}/payment-recipient-authorities", response_model=list[PaymentRecipientAuthorityRead])
def list_hosted_room_payment_recipient_authorities(
    room_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    room = get_room(db, room_id)
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    return payment_recipient_authority_crud.list_payment_recipient_authorities_for_room_owned_by(db, user, room)


@router.post(
    "/rooms/{room_id}/payment-recipient-authorities", response_model=PaymentRecipientAuthorityRead,
    status_code=status.HTTP_201_CREATED,
)
def declare_hosted_payment_recipient_authority(
    room_id: int,
    payload: PaymentRecipientAuthorityDeclare,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.room_id != room_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "roomId in the body must match the room in the URL")
    room = get_room(db, room_id)
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    # Wireframe A's default choice, "Me / the property owner" -- the
    # frontend has no reason to otherwise know its own party id.
    recipient_party_id = payload.recipient_party_id if payload.recipient_party_id is not None else user.party_id
    record, _raw_code = payment_recipient_authority_crud.declare_payment_recipient_authority(
        db, user, room, recipient_party_id=recipient_party_id,
        relationship_type=payload.relationship_type, evidence_ref=payload.evidence_ref,
    )
    emit_event(
        db, "payment_recipient_authority.declared", "payment_recipient_authority", str(record.id),
        {"roomId": room_id}, correlation_id=get_correlation_id(request),
    )
    db.commit()
    return record


def _get_room_scoped_payment_recipient_authority(db: Session, room_id: int, authority_id: int) -> PaymentRecipientAuthority:
    record = payment_recipient_authority_crud.get_payment_recipient_authority_or_404(db, authority_id)
    if record.room_id != room_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment recipient authority not found for this room")
    return record


@router.post("/rooms/{room_id}/payment-recipient-authorities/{authority_id}/resend-change-code")
def resend_hosted_payment_recipient_authority_change_code(
    room_id: int, authority_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-LINK-003 Section 14.1's step-up code, resent -- only the
    submitting room's own owner, same ownership check
    declare_payment_recipient_authority/confirm_payment_recipient_authority_change
    already enforce."""
    room = get_room(db, room_id)
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    if not user.party_id or room.property.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only manage payments for your own room")
    record = _get_room_scoped_payment_recipient_authority(db, room_id, authority_id)
    payment_recipient_authority_crud.resend_payment_recipient_authority_change_code(db, record, user)
    return {"sent": True}


@router.post(
    "/rooms/{room_id}/payment-recipient-authorities/{authority_id}/confirm-change",
    response_model=PaymentRecipientAuthorityRead,
)
def confirm_hosted_payment_recipient_authority_change(
    room_id: int, authority_id: int, payload: PaymentRecipientAuthorityConfirmChange, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    room = get_room(db, room_id)
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    if not user.party_id or room.property.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only manage payments for your own room")
    record = _get_room_scoped_payment_recipient_authority(db, room_id, authority_id)
    updated = payment_recipient_authority_crud.confirm_payment_recipient_authority_change(db, record, user, payload.code)
    emit_event(
        db, "payment_recipient_authority.change_confirmed", "payment_recipient_authority", str(updated.id),
        {"roomId": room_id}, correlation_id=get_correlation_id(request),
    )
    db.commit()
    return updated


@router.get("/rooms/{room_id}/property-verifications", response_model=list[PropertyVerificationRead])
def list_hosted_room_property_verifications(
    room_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    room = get_room(db, room_id)
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    return property_verification_crud.list_property_verifications_for_room_owned_by(db, user, room)


@router.post(
    "/rooms/{room_id}/property-verifications", response_model=PropertyVerificationRead, status_code=status.HTTP_201_CREATED,
)
def declare_hosted_property_verification(
    room_id: int,
    payload: PropertyVerificationDeclare,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.room_id != room_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "roomId in the body must match the room in the URL")
    room = get_room(db, room_id)
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    record = property_verification_crud.declare_property_verification(db, user, room, evidence_ref=payload.evidence_ref)
    emit_event(
        db, "property_verification.declared", "property_verification", str(record.id),
        {"roomId": room_id}, correlation_id=get_correlation_id(request),
    )
    db.commit()
    return record


# --- Rental Transaction Record: host-facing read-only view --------------
# Same computed-composite build as the renter route (user_rentals.py's own
# GET .../occupancies/{id}/transaction-record) -- ownership is checked
# against the room's own owner_party_id (this file's established pattern),
# never Listing.owner_id, since a self-service host authenticates as a
# UserAccount, not the legacy AdminUser a Listing.owner_id check assumes.
# include_identity is never set here -- a host must never see the renter's
# own identity-verification claim.


@router.get("/rooms/{room_id}/occupancies", response_model=list[OccupancyRead])
def list_hosted_room_occupancies(
    room_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """The host-facing entry point into the Rental Transaction Record: lets
    a host discover which occupancies (current and past tenancies) exist
    for a room they own, so the UI has an occupancy_id to request a
    transaction record for -- mirrors list_hosted_room_authority_records/
    list_hosted_room_property_verifications above exactly."""
    room = get_room(db, room_id)
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    return [occupancy_crud.to_occupancy_read(db, o) for o in occupancy_crud.list_occupancies_for_room_owned_by(db, user, room)]


@router.get("/occupancies/{occupancy_id}/transaction-record", response_model=RentalTransactionRecordRead)
def get_hosted_rental_transaction_record(
    occupancy_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")
    if not user.party_id or not occupancy.room or occupancy.room.property.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view rental records for your own rooms")
    return build_rental_transaction_record(db, occupancy, include_identity=False)
