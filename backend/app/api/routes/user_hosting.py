from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.correlation import get_correlation_id
from app.core.image_uploads import save_listing_images
from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.crud import authority as authority_crud
from app.crud import leasing as leasing_crud
from app.crud import listing as listing_crud
from app.crud import property_verification as property_verification_crud
from app.crud.property import get_property, get_room, list_rooms_for_property
from app.db.session import get_db
from app.models.user_account import UserAccount
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
    UserAgreementSignRequest,
)
from app.schemas.marketplace import AuthorityRecordDeclare, AuthorityRecordRead, PropertyCreate, PropertyRead, RoomCreate, RoomRead
from app.schemas.listing import ListingCreate, ListingRead, ListingUpdate
from app.schemas.verification import PropertyVerificationDeclare, PropertyVerificationRead

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
    agreement = leasing_crud.create_agreement(db, offer, user, payload.selected_optional_clause_ids)
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
