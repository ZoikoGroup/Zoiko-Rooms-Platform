from datetime import date as date_, datetime, timezone
from io import BytesIO

from fastapi import HTTPException, status
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.agreement_documents import resolve_agreement_document_path, save_agreement_document
from app.core.mailer import send_agreement_executed_email, send_application_decided_email
from app.crud.audit import log_audit_event
from app.crud.eligibility import check_agreement_eligibility, check_offer_eligibility
from app.crud.events import emit_event
from app.crud.guest import get_guest_for_user, get_user_for_guest
from app.crud.ids import dicebear_avatar, new_id
from app.crud.listing import is_listing_available
from app.crud.market_policy import resolve_market_policy
from app.crud import notification as notif_crud
from app.crud.party import assert_provider_access, party_id_for_listing
from app.crud.user import get_user_by_party_id
from app.models.admin_user import AdminUser
from app.models.agreement_amendment import AgreementAmendment
from app.models.agreement_party import AgreementParty
from app.models.agreement_version_detail import AgreementPremises, CommercialTermsSnapshot, ExecutionCertificate
from app.models.disclosure_requirement import DisclosureRequirement
from app.models.finance import OBLIGATION_TYPE_TO_PLANE, Obligation, PaymentSchedule
from app.models.guest import Guest
from app.models.leasing import (
    Agreement,
    AgreementVersion,
    Application,
    ApplicationDecision,
    DocumentArtifact,
    Offer,
    OfferTerms,
    SignatureEvent,
)
from app.models.listing import Listing
from app.models.listing_approval import CURRENT_POLICY_VERSION
from app.models.signature_provider import SignatureRequest
from app.models.user_account import UserAccount
from app.schemas.leasing import ApplicationCreate, ApplicationDecide, ApplicationRead, ApplicationUpdate, OfferTermsCreate
from app.services import inventory as inventory_service
from app.services.agreement_profile import DEFAULT_DISCLOSURES, resolve_agreement_profile
from app.services.overlap import evaluate_occupant_overlap
from app.services.booking_expiry import (
    compute_checkout_deadline,
    compute_confirmation_deadline,
    expire_checkout_if_overdue,
    expire_offer_if_overdue,
)


def to_application_read(application: Application) -> ApplicationRead:
    return ApplicationRead(
        id=application.id,
        listing_id=application.listing_id,
        listing_name=application.listing.name if application.listing else "",
        guest_id=application.guest_id,
        guest_name=application.guest.name,
        guest_email=application.guest.email,
        named_occupant_guest_id=application.named_occupant_guest_id,
        status=application.status,
        message=application.message,
        desired_move_in=application.desired_move_in,
        submitted_at=application.submitted_at,
        updated_at=application.updated_at,
        decisions=list(application.decisions),
        offer=application.offer,
    )


def _resolve_guest(db: Session, data: ApplicationCreate) -> Guest:
    if data.guest_id and data.new_guest:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provide either an existing guestId or newGuest, not both")

    if data.guest_id:
        guest = db.get(Guest, data.guest_id)
        if not guest:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Guest not found")
        return guest

    if data.new_guest:
        existing = db.scalar(select(Guest).where(Guest.email == data.new_guest.email))
        if existing:
            return existing
        guest = Guest(
            id=new_id("G"),
            name=data.new_guest.name,
            email=data.new_guest.email,
            phone=data.new_guest.phone,
            avatar=dicebear_avatar(data.new_guest.name),
            location=data.new_guest.location,
            joined_at=date_.today(),
            status="active",
        )
        db.add(guest)
        db.flush()
        return guest

    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Either guestId or newGuest is required")


def submit_application(db: Session, data: ApplicationCreate) -> Application:
    """Submission alone never creates a rent obligation -- that only happens once an
    Offer is accepted and an Agreement is signed."""
    listing = db.get(Listing, data.listing_id)
    if not listing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")
    if not is_listing_available(db, listing):
        raise HTTPException(status.HTTP_409_CONFLICT, "This listing is not currently accepting applications")

    guest = _resolve_guest(db, data)

    named_occupant_guest_id = data.named_occupant_guest_id
    if named_occupant_guest_id is not None:
        if named_occupant_guest_id == guest.id:
            # Naming the applicant themselves as the occupant is just the
            # default -- store it as None so occupant_guest_id resolves the
            # same way either way, not as a distinct case to reason about.
            named_occupant_guest_id = None
        elif db.get(Guest, named_occupant_guest_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "namedOccupantGuestId does not match an existing guest")

    application = Application(
        listing_id=listing.id,
        guest_id=guest.id,
        named_occupant_guest_id=named_occupant_guest_id,
        message=data.message,
        desired_move_in=data.desired_move_in,
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def list_applications_for(db: Session, admin: AdminUser) -> list[Application]:
    query = (
        select(Application)
        .options(
            joinedload(Application.listing),
            joinedload(Application.guest),
            selectinload(Application.decisions),
            joinedload(Application.offer),
        )
        .order_by(Application.submitted_at.desc())
    )
    if admin.role != "super_admin":
        query = query.join(Listing, Listing.id == Application.listing_id).where(Listing.owner_id == admin.id)
    return list(db.scalars(query))


def get_application_or_404(db: Session, application_id: int) -> Application:
    application = db.get(Application, application_id)
    if not application:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return application


def update_application(db: Session, application: Application, admin: AdminUser, data: ApplicationUpdate) -> Application:
    assert_provider_access(db, admin, party_id_for_listing(application.listing))
    if application.status != "SUBMITTED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a pending application can be edited")

    if data.message is not None:
        application.message = data.message
    if data.desired_move_in is not None:
        application.desired_move_in = data.desired_move_in
    application.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(application)
    return application


def withdraw_application(db: Session, application: Application, admin: AdminUser) -> Application:
    assert_provider_access(db, admin, party_id_for_listing(application.listing))
    if application.status != "SUBMITTED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a pending application can be withdrawn")

    application.status = "WITHDRAWN"
    application.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(application)
    return application


def decide_application(db: Session, application: Application, admin: AdminUser, data: ApplicationDecide) -> ApplicationDecision:
    """Restricted to super_admin at the route level -- applicant screening/approval is
    a platform trust & safety decision, not a provider one, unlike everything after it
    (offer terms, agreement) which stays with the provider."""
    if data.decision not in ("APPROVED", "REJECTED"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "decision must be APPROVED or REJECTED")

    decision = ApplicationDecision(
        application_id=application.id,
        decision=data.decision,
        reason_code=data.reason_code,
        note=data.note,
        decided_by_admin_id=admin.id,
    )
    db.add(decision)
    application.status = "DECIDED"
    application.updated_at = datetime.now(timezone.utc)

    guest = db.get(Guest, application.guest_id)
    if guest:
        verb = "approved" if data.decision == "APPROVED" else "rejected"
        notif_crud.notify_user_by_guest(
            db, guest,
            title=f"Your rental application was {verb}",
            message=data.note or f"Your application for listing {application.listing_id} was {verb}.",
            notification_type=f"application.{verb}",
            related_entity_type="application", related_entity_id=str(application.id),
        )
        renter_user = get_user_for_guest(db, guest)
        if renter_user and application.listing:
            send_application_decided_email(
                renter_user.email, renter_user.full_name, application.listing.name,
                approved=data.decision == "APPROVED",
            )

    # The host has a real stake in this decision too (an approval means they can
    # now build an offer; a rejection means this applicant is off the table) --
    # a separate notification_type from the renter's own, so each recipient's
    # notification links to their own role-appropriate page, and deliberately
    # never includes the admin's internal note/reason_code (that's a platform
    # trust & safety detail, not something to expose to the host).
    listing = application.listing
    if listing and listing.party_id:
        verb = "approved" if data.decision == "APPROVED" else "rejected"
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title=f"An application was {verb}",
            message=f'An application for "{listing.name}" was {verb}.',
            notification_type="application.decided",
            related_entity_type="application", related_entity_id=str(application.id),
        )

    db.commit()
    db.refresh(decision)
    return decision


def get_offer_or_404(db: Session, offer_id: int, correlation_id: str = "") -> Offer:
    offer = db.get(Offer, offer_id)
    if not offer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Offer not found")
    # Lazy expiry (ZR-ENG-CLR-001 Rule 7): no scheduler exists in this stack,
    # so every read of an offer is a checkpoint that self-heals a stale
    # ACCEPTED-past-deadline offer to EXPIRED before anyone acts on it.
    if expire_offer_if_overdue(db, offer, correlation_id=correlation_id):
        db.commit()
        db.refresh(offer)
    return offer


def create_offer(db: Session, application: Application, admin: AdminUser) -> Offer:
    assert_provider_access(db, admin, party_id_for_listing(application.listing))
    reasons = check_offer_eligibility(db, application)
    if reasons:
        raise HTTPException(status.HTTP_409_CONFLICT, {"message": "Not eligible to create an offer", "reasons": reasons})

    offer = Offer(application_id=application.id, listing_id=application.listing_id, guest_id=application.guest_id)
    db.add(offer)
    db.commit()
    db.refresh(offer)
    return offer


def add_offer_terms(db: Session, offer: Offer, admin: AdminUser, data: OfferTermsCreate, correlation_id: str = "") -> OfferTerms:
    """ZR-ENG-CLR-004 AC-06 'A change to rent, dates, parties, premises,
    deposit or other configured material term invalidates a pending signing
    version'. Two cases:

    1. No Agreement exists yet (offer.status in DRAFT/SENT) -- nothing to
       invalidate, this is just ordinary pre-acceptance negotiation.
    2. An Agreement already exists but its current version is still WORKING
       (not yet frozen/executed) -- Section 17's own edge case, 'Host changes
       rent after renter opens agreement': allowed, but the material change
       closes the current version as SUPERSEDED and opens a fresh WORKING
       version from the new terms (see _invalidate_pending_agreement_version).

    Once a version reaches FROZEN/EXECUTED_IMMUTABLE, neither case applies --
    the guard below rejects the request outright, exactly as before."""
    assert_provider_access(db, admin, party_id_for_listing(offer.listing))
    agreement = offer.agreement
    reversioning = False
    if offer.status not in ("DRAFT", "SENT"):
        can_reversion = (
            offer.status == "ACCEPTED"
            and agreement is not None
            and agreement.versions
            and agreement.versions[-1].status == "WORKING"
            and agreement.status not in ("SIGNED", "EXPIRED", "VOID")
        )
        if not can_reversion:
            raise HTTPException(status.HTTP_409_CONFLICT, "Offer terms can only be added while the offer is draft or sent")
        reversioning = True

    # ZR-ENG-CLR-002 AC-02: the quote engine must reject any deposit amount
    # above the resolved market-pack cap -- not a Zoiko-invented number, and
    # not something the UI can silently bypass by omission.
    policy = resolve_market_policy(db)
    max_deposit = round(data.monthly_rent * float(policy.deposit_max_rent_multiple), 2)
    if data.deposit_amount > max_deposit:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Deposit amount {data.deposit_amount:.2f} exceeds the resolved market cap of "
            f"{max_deposit:.2f} ({policy.deposit_max_rent_multiple}x monthly rent, jurisdiction={policy.jurisdiction_code})",
        )

    next_version = offer.current_version + 1
    terms = OfferTerms(
        offer_id=offer.id,
        version=next_version,
        monthly_rent=data.monthly_rent,
        deposit_amount=data.deposit_amount,
        start_date=data.start_date,
        term_months=data.term_months,
    )
    db.add(terms)
    offer.current_version = next_version
    db.flush()

    if reversioning:
        _invalidate_pending_agreement_version(db, agreement, offer, terms, admin, correlation_id=correlation_id)

    db.commit()
    db.refresh(terms)
    return terms


def _invalidate_pending_agreement_version(
    db: Session, agreement: Agreement, offer: Offer, latest_terms: OfferTerms, admin: AdminUser, correlation_id: str = "",
) -> None:
    """ZR-ENG-CLR-004 AC-06/Section 9.2/17.1 'Host changes rent after renter
    opens agreement: Invalidate pending version; return to commercial terms
    approval; generate new version.' Called from add_offer_terms once a new
    OfferTerms row has already been flushed. Closes the current WORKING
    version as SUPERSEDED and opens version_no+1 from the fresh terms --
    never mutates the superseded version's own snapshot/status in place.

    Existing SignatureEvents stay linked to the superseded version_id
    (preserved evidence -- Section 9.2: 'signatures on the superseded
    pending version must not be transplanted'), while Agreement's own
    signed_by_*_at flags reset so the new version genuinely requires fresh
    signatures, and status returns to DRAFT ('return to commercial terms
    approval') -- an admin must re-send it before either party can sign
    again."""
    current_version = agreement.versions[-1]

    listing = offer.listing
    profile = resolve_agreement_profile(db, listing, listing.room)
    if profile is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No approved agreement profile for this listing's jurisdiction -- routed to manual review",
        )

    before_state = f"v{current_version.version_no}:{agreement.status}"
    current_version.status = "SUPERSEDED"

    new_version = AgreementVersion(
        agreement_id=agreement.id, version_no=current_version.version_no + 1, status="WORKING",
        snapshot=_build_agreement_snapshot(offer, profile, latest_terms=latest_terms),
    )
    db.add(new_version)
    db.flush()
    _populate_version_detail_rows(db, new_version, offer, latest_terms)

    agreement.signed_by_provider_at = None
    agreement.signed_by_renter_at = None
    agreement.signature_ref = ""
    agreement.status = "DRAFT"

    log_audit_event(
        db, admin, "agreement.version_superseded", "agreement", str(agreement.id), correlation_id,
        reason="material_term_change", before_state=before_state, after_state=f"v{new_version.version_no}:DRAFT",
    )
    emit_event(
        db, "agreement.version_superseded", "agreement", str(agreement.id),
        {"supersededVersionNo": current_version.version_no, "newVersionNo": new_version.version_no},
        correlation_id=correlation_id,
    )


def _accept_offer_and_hold_room(
    db: Session, offer: Offer, new_status: str, correlation_id: str = "", override_reason: str = "",
) -> None:
    """ZR-ENG-CLR-001 Rule 4: the room is committed the moment its offer is
    accepted -- the earliest point a renter has actually said yes, well
    before an Agreement or Occupancy exists. Rule 7: also starts the
    accepted-booking confirmation clock (default 24h,
    settings.offer_acceptance_confirmation_hours) -- see
    services/booking_expiry.py.

    Rule 6/Section 9: also where occupant-overlap risk is evaluated
    (services/overlap.py) -- a BLOCK tier refuses acceptance unless
    override_reason is supplied (admin-only; user_accept_offer never passes
    one, so a renter can never self-override a block). Skipped entirely if
    the offer has no terms yet -- there's no interval to compare.

    The status flip and the hold creation happen inside one SAVEPOINT
    (db.begin_nested()) -- entering a SAVEPOINT flushes whatever was already
    dirty into the *outer* transaction first, so both the status change and
    the hold attempt must originate *inside* this block, not before it, or a
    failed hold wouldn't actually undo the status flip. On a 409 here, the
    offer is left exactly as it was (still SENT), never silently ACCEPTED
    with no room actually held for it."""
    room_id = offer.listing.room_id

    risk_tier, risk_reason = "NONE", ""
    latest_terms = offer.terms[-1] if offer.terms else None
    if latest_terms is not None:
        risk_tier, risk_reason = evaluate_occupant_overlap(
            db,
            occupant_guest_id=offer.application.occupant_guest_id,
            listing_id=offer.listing_id,
            start_date=latest_terms.start_date,
            term_months=latest_terms.term_months,
            exclude_offer_id=offer.id,
        )
        if risk_tier == "BLOCK" and not override_reason.strip():
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {"message": "This occupant already has an overlapping active booking elsewhere", "reason": risk_reason},
            )

    with db.begin_nested():
        offer.status = new_status
        offer.accepted_at = datetime.now(timezone.utc)
        offer.confirmation_expires_at = compute_confirmation_deadline(offer.accepted_at, offer.listing.market_release)
        offer.occupant_risk_tier = risk_tier
        offer.occupant_risk_reason = (
            f"{risk_reason} (accepted with override: {override_reason.strip()})"
            if risk_tier == "BLOCK" and override_reason.strip()
            else risk_reason
        )
        if room_id is not None:
            inventory_service.create_hold(
                db, room_id=room_id, source_type="offer", source_id=offer.id, correlation_id=correlation_id,
            )


def _release_room_hold_for_offer(db: Session, offer: Offer, reason: str, correlation_id: str = "") -> None:
    inventory_service.release_hold(
        db, source_type="offer", source_id=offer.id, reason=reason, correlation_id=correlation_id,
    )


def set_offer_status(
    db: Session, offer: Offer, admin: AdminUser, new_status: str, correlation_id: str = "", override_reason: str = "",
) -> Offer:
    assert_provider_access(db, admin, party_id_for_listing(offer.listing))
    if new_status in ("ACCEPTED", "DECLINED"):
        _assert_renter_has_no_account(db, offer.guest_id, action="accept or decline this offer")
    if new_status == "ACCEPTED":
        _accept_offer_and_hold_room(db, offer, new_status, correlation_id=correlation_id, override_reason=override_reason)
    else:
        offer.status = new_status
        if new_status in ("DECLINED", "EXPIRED", "WITHDRAWN"):
            _release_room_hold_for_offer(
                db, offer, reason=f"offer_{new_status.lower()}", correlation_id=correlation_id,
            )
    db.commit()
    db.refresh(offer)

    listing = offer.listing
    if new_status == "SENT":
        _notify_offer_guest(db, offer, title="You have a new rental offer", notification_type="offer.sent")
    elif new_status == "ACCEPTED":
        # The authoritative "accepted" state for this workflow -- both sides are
        # told only once it's actually committed here, never earlier.
        _notify_offer_guest(
            db, offer, title="Your offer was accepted",
            message=f'Your offer for "{listing.name}" has been accepted.', notification_type="offer.accepted",
        )
        if listing and listing.party_id:
            notif_crud.notify_user_by_party(
                db, listing.party_id,
                title="An offer was accepted",
                message=f'The offer for "{listing.name}" has been accepted.',
                notification_type="offer.accepted_for_host",
                related_entity_type="offer", related_entity_id=str(offer.id),
            )
    elif new_status == "DECLINED":
        if listing and listing.party_id:
            notif_crud.notify_user_by_party(
                db, listing.party_id,
                title="An offer was declined",
                message=f'The offer for "{listing.name}" was declined.',
                notification_type="offer.declined_for_host",
                related_entity_type="offer", related_entity_id=str(offer.id),
            )
    return offer


def _assert_renter_has_no_account(db: Session, guest_id: str, *, action: str) -> None:
    """Accepting/declining an offer or signing as renter is admin-doable only
    for a walk-in guest with no Zoiko login -- a renter with a real account
    must take that action themselves (see user_accept_offer/user_decline_offer/
    user_sign_agreement), so an admin can't silently supply consent for them."""
    guest = db.get(Guest, guest_id)
    if guest and guest.user_account_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This renter has a Zoiko account and must {action} themselves.",
        )


def _notify_offer_guest(db: Session, offer: Offer, *, title: str, notification_type: str, message: str = "") -> None:
    guest = db.get(Guest, offer.guest_id)
    if not guest:
        return
    notif_crud.notify_user_by_guest(
        db, guest,
        title=title,
        message=message or f"{title} for \"{offer.listing.name if offer.listing else 'your application'}\".",
        notification_type=notification_type,
        related_entity_type="offer", related_entity_id=str(offer.id),
    )


def _guest_owns_offer(db: Session, user: UserAccount, offer: Offer) -> None:
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This offer does not belong to you")


def user_accept_offer(
    db: Session, user: UserAccount, offer: Offer, correlation_id: str = "", override_reason: str = "",
) -> Offer:
    """The renter accepting their own offer -- previously only an admin could
    do this on the renter's behalf, with no real consent captured from the
    renter's own session. override_reason: only consulted when
    occupant-overlap comes back BLOCK (Rule 6/Section 9) -- the renter
    self-declares why (e.g. a confirmed relocation), captured on the offer
    for later admin review; this is not a bypass of the risk check, it's the
    "require an exception" half of the spec's own "Block confirmation or
    require exception" rule."""
    _guest_owns_offer(db, user, offer)
    if offer.status != "SENT":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a sent offer can be accepted")
    _accept_offer_and_hold_room(db, offer, "ACCEPTED", correlation_id=correlation_id, override_reason=override_reason)
    db.commit()
    db.refresh(offer)
    return offer


def user_decline_offer(db: Session, user: UserAccount, offer: Offer, correlation_id: str = "") -> Offer:
    _guest_owns_offer(db, user, offer)
    if offer.status != "SENT":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a sent offer can be declined")
    offer.status = "DECLINED"
    _release_room_hold_for_offer(db, offer, reason="offer_declined", correlation_id=correlation_id)
    db.commit()
    db.refresh(offer)
    return offer


def _build_agreement_snapshot(
    offer: Offer, profile, *, latest_terms: OfferTerms | None = None, selected_optional_clause_ids: tuple[str, ...] = (),
) -> dict:
    """ZR-ENG-CLR-004 Section 13.3 'Snapshot rule': every material source
    value used to generate the agreement, frozen at generation time. Later
    edits to the live Listing/Offer/Guest/Room rows must never change what
    an already-generated version renders (AC-27) -- see
    generate_agreement_pdf, which reads only from this dict, never from
    `offer`/`offer.listing`/`offer.guest` directly.

    latest_terms defaults to offer.terms[-1] for the normal create_agreement
    call site; _invalidate_pending_agreement_version passes the just-flushed
    OfferTerms row explicitly, since relying on the (possibly stale, until
    refreshed) offer.terms collection inside the same transaction would be
    fragile."""
    listing = offer.listing
    room = listing.room
    guest = offer.guest
    latest_terms = latest_terms or offer.terms[-1]
    provider_name = listing.contact_name or (listing.owner.full_name if listing.owner else "Host")
    provider_email = listing.contact_email or (listing.owner.email if listing.owner else "")

    # AC-17: only ids drawn from the resolved profile's own approved
    # optional-clause allow-list ever reach here -- see create_agreement's
    # validation. clause_ids therefore always stays an allow-listed set,
    # never free text.
    all_clause_ids = sorted(set(profile.clause_ids) | set(selected_optional_clause_ids))

    return {
        "listing_id": listing.id,
        "listing_name": listing.name,
        "listing_location": listing.location,
        "listing_city": listing.city,
        "room_size": room.size if room else None,
        "room_has_ensuite": room.has_ensuite if room else None,
        "provider_name": provider_name,
        "provider_email": provider_email,
        "renter_name": guest.name,
        "renter_email": guest.email,
        "renter_phone": guest.phone,
        "monthly_rent": float(latest_terms.monthly_rent),
        "deposit_amount": float(latest_terms.deposit_amount),
        "start_date": latest_terms.start_date.isoformat(),
        "term_months": latest_terms.term_months,
        "agreement_class": profile.agreement_class,
        "form_mode": profile.form_mode,
        # AC-04: which exact AgreementFormTemplate this version was
        # generated against, for modes B/C/D -- None for native mode A.
        "form_template_id": profile.form_template_id,
        "form_template_version": profile.form_template_version,
        "clause_ids": all_clause_ids,
        "optional_clause_ids_selected": sorted(selected_optional_clause_ids),
        # AC-08/AC-25: which exact ClauseDefinition.version backs each
        # included clause_id, plus a short content-addressed tag for the
        # whole set -- see services/agreement_profile.py:_compute_market_pack_version.
        "clause_versions": {cid: v for cid, v in profile.clause_versions.items() if cid in all_clause_ids},
        "market_pack_version": profile.market_pack_version,
        "required_signers": list(profile.required_signers),
        "signing_order": profile.signing_order,
        "assurance_level": profile.assurance_level,
    }


def _populate_agreement_parties(db: Session, agreement: Agreement, offer: Offer) -> None:
    """ZR-ENG-CLR-004 Section 13.1 agreement_party: the real per-party
    roster, populated once at create_agreement time from the same verified
    listing/guest facts _build_agreement_snapshot reads -- never Host free
    text (Section 5.2 lists 'legal label/authority of a party' among the
    fields Host must not control)."""
    listing = offer.listing
    guest = offer.guest
    provider_name = listing.contact_name or (listing.owner.full_name if listing.owner else "Host")
    provider_email = listing.contact_email or (listing.owner.email if listing.owner else "")
    db.add(AgreementParty(
        agreement_id=agreement.id, role="provider", party_type="individual",
        legal_name=provider_name, contact_email=provider_email,
    ))
    db.add(AgreementParty(
        agreement_id=agreement.id, role="renter", party_type="individual",
        legal_name=guest.name, contact_email=guest.email,
    ))


def _populate_version_detail_rows(db: Session, version: AgreementVersion, offer: Offer, latest_terms: OfferTerms) -> None:
    """Section 13.1 agreement_premises/commercial_terms_snapshot: real
    queryable tables populated alongside the version's own JSON snapshot
    (_build_agreement_snapshot) -- a structured mirror of the same frozen
    facts, not a second source of truth. Requires version.id, so callers
    must db.flush() after db.add(version) first."""
    listing = offer.listing
    room = listing.room
    db.add(AgreementPremises(
        agreement_version_id=version.id, listing_id=listing.id, room_id=room.id if room else None,
        address=listing.location, city=listing.city,
        room_size=room.size if room else None, has_ensuite=bool(room.has_ensuite) if room else False,
        shared_areas=[],
    ))
    db.add(CommercialTermsSnapshot(
        agreement_version_id=version.id, monthly_rent=latest_terms.monthly_rent,
        deposit_amount=latest_terms.deposit_amount, start_date=latest_terms.start_date,
        term_months=latest_terms.term_months,
    ))


def create_agreement(
    db: Session, offer: Offer, admin: AdminUser, selected_optional_clause_ids: list[str] | None = None,
) -> Agreement:
    assert_provider_access(db, admin, party_id_for_listing(offer.listing))
    reasons = check_agreement_eligibility(db, offer)
    if reasons:
        raise HTTPException(status.HTTP_409_CONFLICT, {"message": "Not eligible to create an agreement", "reasons": reasons})

    # ZR-ENG-CLR-004 Section 3.3 'Fail closed': no approved
    # jurisdiction/agreement profile means no automated contract -- never a
    # generic fallback agreement (AC-01/AC-32).
    listing = offer.listing
    profile = resolve_agreement_profile(db, listing, listing.room)
    if profile is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No approved agreement profile for this listing's jurisdiction -- routed to manual review",
        )

    # AC-17 'Host special terms use approved options; uncontrolled legal free
    # text cannot enter executable agreement': the only input this function
    # accepts is a list of ids, and every id must already be in the
    # resolved profile's own approved optional-clause set -- there is no
    # code path from here to free-form clause content.
    selected = list(dict.fromkeys(selected_optional_clause_ids or []))
    invalid = sorted(set(selected) - set(profile.optional_clause_ids))
    if invalid:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Not an approved optional clause for this agreement: {invalid}",
        )

    agreement = Agreement(offer_id=offer.id)
    db.add(agreement)
    db.flush()

    latest_terms = offer.terms[-1]
    version = AgreementVersion(
        agreement_id=agreement.id, version_no=1, status="WORKING",
        snapshot=_build_agreement_snapshot(offer, profile, selected_optional_clause_ids=tuple(selected)),
    )
    db.add(version)
    db.flush()
    _populate_version_detail_rows(db, version, offer, latest_terms)
    _populate_agreement_parties(db, agreement, offer)

    # ZR-ENG-CLR-004 Section 6.8/13.1: the disclosure checklist this
    # agreement must clear before signing -- see _apply_signature's gate
    # below (AC-15). Seeded once, at generation time, same as the clause
    # registry (services/agreement_profile.py:DEFAULT_DISCLOSURES).
    for disclosure_type, title, required in DEFAULT_DISCLOSURES:
        db.add(DisclosureRequirement(
            agreement_id=agreement.id, disclosure_type=disclosure_type, title=title, required=required,
        ))

    # ZR-ENG-CLR-005 AC-02/AC-07: the versioned plan this agreement's RENT
    # obligations are traceable to -- frozen amount/first_due, same snapshot
    # discipline as the AgreementVersion above. See models/finance.py:
    # PaymentSchedule for why cadence/status stay single-valued in this build.
    schedule = PaymentSchedule(
        agreement_id=agreement.id,
        amount=latest_terms.monthly_rent,
        first_due=latest_terms.start_date,
        anchor_day=latest_terms.start_date.day,
    )
    db.add(schedule)
    db.flush()

    db.add(
        Obligation(
            obligation_type="RENT",
            money_plane=OBLIGATION_TYPE_TO_PLANE["RENT"],
            amount=latest_terms.monthly_rent,
            due_date=latest_terms.start_date,
            agreement_id=agreement.id,
            schedule_id=schedule.id,
        )
    )
    db.add(
        Obligation(
            obligation_type="DEPOSIT",
            money_plane=OBLIGATION_TYPE_TO_PLANE["DEPOSIT"],
            amount=latest_terms.deposit_amount,
            due_date=latest_terms.start_date,
            agreement_id=agreement.id,
        )
    )
    inventory_service.mark_hold_booked(db, source_type="offer", source_id=offer.id)
    db.commit()
    db.refresh(agreement)
    return agreement


def get_agreement_or_404(db: Session, agreement_id: int, correlation_id: str = "") -> Agreement:
    agreement = db.get(Agreement, agreement_id)
    if not agreement:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agreement not found")
    # Lazy signing-deadline expiry (ZR-ENG-CLR-004 4.5) -- reading the
    # agreement directly must self-heal a stale SENT/PARTIALLY_EXECUTED
    # agreement to EXPIRED too, not only a read of the underlying offer.
    if expire_offer_if_overdue(db, agreement.offer, correlation_id=correlation_id):
        db.commit()
        db.refresh(agreement)
    # Lazy checkout-lock expiry (ZR-ENG-CLR-001 Rule 7, 10.2), same self-healing
    # pattern as get_offer_or_404's lazy acceptance-window expiry above.
    if expire_checkout_if_overdue(db, agreement, correlation_id=correlation_id):
        db.commit()
        db.refresh(agreement)
    return agreement


def create_signature_requests(db: Session, agreement: Agreement, version: AgreementVersion) -> list[SignatureRequest]:
    """ZR-ENG-CLR-004 Section 13.1 signature_request: one PENDING row per
    required signer for this version, so the AC-23/24 provider-dispatch
    machinery (crud/signature_provider.py) has something to target --
    called from send_agreement below and from
    crud/agreement_amendments.py:approve_amendment for a new amendment
    version (which skips the SENT step entirely)."""
    existing = list(db.scalars(select(SignatureRequest).where(SignatureRequest.agreement_version_id == version.id)))
    if existing:
        return existing

    required_signers = version.snapshot.get("required_signers", ("provider", "renter"))
    method = version.snapshot.get("assurance_level", "SIMPLE_ESIGN")
    deadline = agreement.offer.confirmation_expires_at
    requests = []
    for role in required_signers:
        sr = SignatureRequest(
            agreement_id=agreement.id, agreement_version_id=version.id, party_role=role,
            method=method, deadline=deadline,
        )
        db.add(sr)
        requests.append(sr)
    db.flush()
    return requests


def send_agreement(db: Session, agreement: Agreement, admin: AdminUser) -> Agreement:
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    agreement.status = "SENT"
    create_signature_requests(db, agreement, agreement.versions[-1])
    db.commit()
    db.refresh(agreement)
    _notify_offer_guest(
        db, agreement.offer,
        title="Your rental agreement is ready to sign",
        notification_type="agreement.sent",
    )
    return agreement


def get_disclosure_or_404(db: Session, agreement: Agreement, disclosure_id: int) -> DisclosureRequirement:
    disclosure = db.get(DisclosureRequirement, disclosure_id)
    if not disclosure or disclosure.agreement_id != agreement.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Disclosure requirement not found")
    return disclosure


def _generate_disclosure_document(disclosure: DisclosureRequirement) -> bytes:
    """AC-16 'agreement package can include required attachments': a real,
    hash-verifiable rendered document per disclosure -- generated exactly
    once, at first delivery (see deliver_disclosure below), never
    regenerated after. Same placeholder-content status as the disclosure
    titles themselves (see models/disclosure_requirement.py's own warning):
    this is NOT counsel-approved legal wording."""
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4
    x, y = 20 * mm, height - 25 * mm
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(x, y, disclosure.title or disclosure.disclosure_type)
    y -= 10 * mm
    pdf.setFont("Helvetica", 9)
    pdf.drawString(x, y, "Internal placeholder disclosure content -- not counsel-approved legal wording.")
    y -= 6 * mm
    pdf.drawString(x, y, "See ZR-ENG-CLR-004 LEGAL CONTROL doctrine.")
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()


ALLOWED_DELIVERY_CHANNELS = ("IN_APP", "EMAIL", "POST", "ACCESSIBLE_TEXT")


def deliver_disclosure(
    db: Session, agreement: Agreement, disclosure: DisclosureRequirement, admin: AdminUser, *,
    to_party: str = "renter", delivery_channel: str = "IN_APP",
) -> DisclosureRequirement:
    """ZR-ENG-CLR-004 Section 9.3 disclosure lifecycle: REQUIRED -> ...
    DELIVERED. Idempotent -- delivering an already-delivered/acknowledged
    disclosure is a no-op, not an error.

    AC-16: also renders and persists a real attached document the first time
    a disclosure is delivered (skipped on a later idempotent call if one
    already exists), and records which party it was delivered to -- these
    statutory disclosures are delivered to the renter/tenant by default (see
    models/disclosure_requirement.py:delivered_to_party's own docstring on
    why this isn't a full N-party matrix). AC-28: delivery_channel records
    which alternate path was actually used -- POST/ACCESSIBLE_TEXT are real,
    selectable alternatives to the default in-app PDF, not just enum values
    nothing sets (see GET .../accessible-text for the ACCESSIBLE_TEXT
    rendering itself)."""
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    if delivery_channel not in ALLOWED_DELIVERY_CHANNELS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"deliveryChannel must be one of {list(ALLOWED_DELIVERY_CHANNELS)}")
    if disclosure.status in ("DELIVERED", "ACKNOWLEDGED"):
        return disclosure

    if not disclosure.document_storage_ref:
        pdf_bytes = _generate_disclosure_document(disclosure)
        storage_ref, content_hash = save_agreement_document(pdf_bytes)
        disclosure.document_storage_ref = storage_ref
        disclosure.document_content_hash = content_hash

    disclosure.status = "DELIVERED"
    disclosure.delivered_at = datetime.now(timezone.utc)
    disclosure.delivered_to_party = to_party
    disclosure.delivery_channel = delivery_channel
    db.commit()
    db.refresh(disclosure)
    return disclosure


def user_acknowledge_disclosure(
    db: Session, user: UserAccount, agreement: Agreement, disclosure: DisclosureRequirement,
) -> DisclosureRequirement:
    """ZR-ENG-CLR-004 Section 9.3 disclosure lifecycle's ACKNOWLEDGED step --
    the renter's own confirmation of receipt, distinct from deliver_disclosure
    (which only records that Zoiko made it available). Only reachable once
    DELIVERED; the _apply_signature gate already accepts either DELIVERED or
    ACKNOWLEDGED, so acknowledging is never required to unblock signing --
    it's the positive record that the renter actually opened it, when a
    market pack cares to check for that reference (AC-15)."""
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")
    if disclosure.agreement_id != agreement.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Disclosure requirement not found")
    if disclosure.status == "ACKNOWLEDGED":
        return disclosure
    if disclosure.status != "DELIVERED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a delivered disclosure can be acknowledged")

    disclosure.status = "ACKNOWLEDGED"
    disclosure.acknowledged_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(disclosure)
    return disclosure


def _all_initial_obligations_paid(agreement: Agreement) -> bool:
    """Same clearance test as check_move_in_eligibility's own unpaid-obligations
    check -- kept in one place so 'fully paid' can't drift between the two."""
    return bool(agreement.obligations) and all(o.status in ("PAID", "WAIVED") for o in agreement.obligations)


# ZR-ENG-CLR-004 AC-11: every method the spec's own 4.4 table names.
# METHOD_REQUIRED_EVIDENCE lists exactly which evidence_metadata keys each
# one requires -- structurally distinct evidence per assurance level, not
# just a different label on the same SIMPLE_ESIGN evidence shape. ADVANCED
# is enforced separately (a verified identity check, not a metadata key) --
# see _apply_signature's ADVANCED_ESIGN branch below.
ALLOWED_SIGNATURE_METHODS = (
    "ACKNOWLEDGMENT", "SIMPLE_ESIGN", "ADVANCED_ESIGN", "QUALIFIED_ESIGN", "WITNESSED_ESIGN", "NOTARIZED", "WET_INK",
)
METHOD_REQUIRED_EVIDENCE: dict[str, tuple[str, ...]] = {
    "ACKNOWLEDGMENT": ("consent_statement",),
    "SIMPLE_ESIGN": (),
    "ADVANCED_ESIGN": (),
    "QUALIFIED_ESIGN": ("trust_service_certificate_ref",),
    "WITNESSED_ESIGN": ("witness_name", "witness_contact"),
    "NOTARIZED": ("notary_name", "notary_license_ref"),
    "WET_INK": (),
}


def _resolve_party_id_for_signer(db: Session, agreement: Agreement, as_party: str) -> int | None:
    if as_party == "provider":
        return party_id_for_listing(agreement.offer.listing)
    guest = db.get(Guest, agreement.offer.guest_id)
    if guest and guest.user_account_id:
        user = db.get(UserAccount, guest.user_account_id)
        return user.party_id if user else None
    return None


def _apply_signature(
    db: Session, agreement: Agreement, as_party: str, *, method: str = "SIMPLE_ESIGN", evidence_ref: str = "",
    evidence_hash: str = "", evidence_metadata: dict | None = None,
) -> Agreement:
    """Simulated e-signature -- no real DocuSign-style provider is connected. Records
    a signature token and timestamp per party.

    ZR-ENG-CLR-001 Rule 7: both signatures alone are not enough to reach the
    terminal SIGNED state -- a checkout/payment window opens instead
    (PAYMENT_IN_PROGRESS, default 30 min, see services/booking_expiry.py),
    unless every initial obligation already cleared before the second
    signature landed (a renter who pays first, then signs last), in which case
    there's nothing left to wait for and SIGNED is reached immediately.
    confirm_agreement_payment below is the only other path to SIGNED.

    ZR-ENG-CLR-004 Section 8.1/AC-10: required_signers comes from the
    resolved profile snapshot, not a hardcoded literal -- see
    services/agreement_profile.py:AgreementProfile.required_signers. Every
    profile today resolves to exactly ("provider", "renter") (that's still
    all Agreement's two signed_by_*_at columns can track), so this has the
    same practical effect as the old hardcoded check, but the source of
    truth for "who may sign" is now the profile, not this function."""
    required_signers = agreement.versions[-1].snapshot.get("required_signers", ("provider", "renter"))
    if as_party not in required_signers:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"asParty must be one of {list(required_signers)} for this agreement",
        )

    if method not in ALLOWED_SIGNATURE_METHODS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"method must be one of {list(ALLOWED_SIGNATURE_METHODS)}")

    evidence_metadata = evidence_metadata or {}
    required_keys = METHOD_REQUIRED_EVIDENCE.get(method, ())
    missing_keys = [k for k in required_keys if not str(evidence_metadata.get(k, "")).strip()]
    if missing_keys:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{method} requires evidence field(s): {missing_keys}",
        )

    # AC-11 ADVANCED_ESIGN 'Higher identity/control/integrity requirements;
    # provider capability must be verified': the concrete, checkable version
    # of that is requiring the signer's own party to already have a
    # currently-verified IdentityVerification record -- reusing the same
    # verification this codebase already gates publication/applications on
    # (crud/listing.py, api/routes/user_rentals.py), not a new invented
    # check.
    if method == "ADVANCED_ESIGN":
        from app.crud.identity_verification import get_verified_identity_for_party

        signer_party_id = _resolve_party_id_for_signer(db, agreement, as_party)
        if not signer_party_id or not get_verified_identity_for_party(db, signer_party_id):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "ADVANCED_ESIGN requires the signer to have a currently-verified identity on file",
            )

    # ZR-ENG-CLR-004 AC-15: "Execution blocked when a legally pre-signature
    # disclosure has not been delivered." Checked on every signature attempt
    # (not just the first), same as required_signers above -- a disclosure
    # could still be missing when the second signer attempts to sign even if
    # it was fine for the first.
    undelivered = [
        d.title or d.disclosure_type for d in agreement.disclosures
        if d.required and d.status == "REQUIRED_MISSING"
    ]
    if undelivered:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": "Required disclosures have not been delivered", "missingDisclosures": undelivered},
        )

    now = datetime.now(timezone.utc)
    if as_party == "provider":
        agreement.signed_by_provider_at = now
        signer_identifier = str(agreement.offer.listing.owner_id or agreement.offer.listing.party_id or "")
    else:
        agreement.signed_by_renter_at = now
        signer_identifier = agreement.offer.guest_id
    if not agreement.signature_ref:
        agreement.signature_ref = new_id("SIG")

    # ZR-ENG-CLR-004 Section 8.2: immutable per-signer evidence, one row per
    # signature event -- recorded regardless of which state this signature
    # ultimately leads to below.
    db.add(SignatureEvent(
        agreement_id=agreement.id,
        agreement_version_id=agreement.versions[-1].id,
        signer_role=as_party,
        signer_identifier=signer_identifier,
        method=method,
        document_hash=evidence_hash,
        evidence_storage_ref=evidence_ref,
        evidence_metadata=evidence_metadata,
    ))

    # ZR-ENG-CLR-004 Section 13.1 signature_request: whichever PENDING/
    # DISPATCHED request this signature satisfies is completed here too --
    # every entrypoint into signing (click, wet-ink, provider webhook)
    # shares this one function, so this is the single place completion is
    # ever recorded.
    pending_request = db.scalar(
        select(SignatureRequest).where(
            SignatureRequest.agreement_version_id == agreement.versions[-1].id,
            SignatureRequest.party_role == as_party,
            SignatureRequest.status.in_(("PENDING", "DISPATCHED")),
        )
    )
    if pending_request is not None:
        pending_request.status = "COMPLETED"
        pending_request.completed_at = now

    if agreement.signed_by_provider_at and agreement.signed_by_renter_at:
        if _all_initial_obligations_paid(agreement):
            agreement.status = "SIGNED"
            agreement.payment_session_expires_at = None

            # Only the actual EXECUTED state triggers this -- a single signature
            # (either party) is not yet an executed agreement.
            listing = agreement.offer.listing
            guest = db.get(Guest, agreement.offer.guest_id)
            if guest:
                notif_crud.notify_user_by_guest(
                    db, guest,
                    title="Your rental agreement is signed",
                    message=f'Your agreement for "{listing.name}" has been fully executed.',
                    notification_type="agreement.signed",
                    related_entity_type="agreement", related_entity_id=str(agreement.id),
                )
            if listing and listing.party_id:
                notif_crud.notify_user_by_party(
                    db, listing.party_id,
                    title="A rental agreement is signed",
                    message=f'The agreement for "{listing.name}" has been fully executed.',
                    notification_type="agreement.signed_for_host",
                    related_entity_type="agreement", related_entity_id=str(agreement.id),
                )

            renter_user = get_user_for_guest(db, guest) if guest else None
            if renter_user:
                send_agreement_executed_email(renter_user.email, renter_user.full_name, listing.name)
            host_user = get_user_by_party_id(db, listing.party_id) if listing else None
            if host_user:
                send_agreement_executed_email(host_user.email, host_user.full_name, listing.name)
        else:
            agreement.status = "PAYMENT_IN_PROGRESS"
            agreement.payment_session_expires_at = compute_checkout_deadline(now, agreement.offer.listing.market_release)
    elif agreement.status in ("SENT", "AMENDMENT_PENDING"):
        # ZR-ENG-CLR-004 AC-12: exactly one required signature recorded --
        # a distinct, visible state from "not yet signed at all", never
        # itself treated as EXECUTED. AMENDMENT_PENDING behaves exactly like
        # SENT here (see models/leasing.py:AGREEMENT_STATUSES' own note).
        agreement.status = "PARTIALLY_EXECUTED"

    db.commit()
    db.refresh(agreement)
    if agreement.status == "SIGNED":
        freeze_agreement_version(db, agreement)
        _notify_offer_guest(
            db, agreement.offer,
            title="Your rental agreement is fully signed",
            notification_type="agreement.signed",
        )
    elif agreement.status == "PAYMENT_IN_PROGRESS":
        _notify_offer_guest(
            db, agreement.offer,
            title="Complete payment to confirm your rental agreement",
            notification_type="agreement.payment_due",
        )
    return agreement


def confirm_agreement_payment(db: Session, agreement: Agreement, correlation_id: str = "") -> Agreement:
    """ZR-ENG-CLR-001 Rule 7: 'Payment success must be verified server-to-
    server/provider-side before confirmation.' Called from
    crud/finance.py:confirm_payment once an obligation tied to this agreement
    clears -- the only path (besides _apply_signature's already-paid shortcut
    above) that can move a PAYMENT_IN_PROGRESS/PAYMENT_PENDING agreement to
    the terminal SIGNED state. A no-op otherwise -- not waiting on payment, or
    not every obligation clear yet."""
    if agreement.status not in ("PAYMENT_IN_PROGRESS", "PAYMENT_PENDING"):
        return agreement
    if not _all_initial_obligations_paid(agreement):
        return agreement

    before_state = agreement.status
    agreement.status = "SIGNED"
    agreement.payment_session_expires_at = None
    db.commit()
    db.refresh(agreement)
    freeze_agreement_version(db, agreement)
    log_audit_event(
        db, None, "agreement.payment_confirmed", "agreement", str(agreement.id), correlation_id,
        before_state=before_state, after_state=agreement.status, policy_version=CURRENT_POLICY_VERSION,
    )
    emit_event(
        db, "agreement.signed", "agreement", str(agreement.id), {},
        correlation_id=correlation_id, idempotency_key=f"agreement.signed:{agreement.id}",
    )
    _notify_offer_guest(
        db, agreement.offer,
        title="Your rental agreement is fully signed",
        notification_type="agreement.signed",
    )
    return agreement


def sign_agreement(
    db: Session, agreement: Agreement, as_party: str, admin: AdminUser, *,
    method: str = "SIMPLE_ESIGN", evidence_metadata: dict | None = None,
) -> Agreement:
    """Admin-attested signature -- for a renter who has no Zoiko login of their
    own (a walk-in guest the provider is onboarding manually), the admin
    records that a signature was collected out of band. A renter with a real
    account can't be signed for this way -- enforced below, not just documented
    -- they must sign themselves via user_sign_agreement instead.

    AC-11: method/evidence_metadata let an admin record ACKNOWLEDGMENT,
    ADVANCED_ESIGN, QUALIFIED_ESIGN, WITNESSED_ESIGN or NOTARIZED instead of
    the SIMPLE_ESIGN default -- see _apply_signature's
    METHOD_REQUIRED_EVIDENCE for what each one requires."""
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    if as_party == "renter":
        _assert_renter_has_no_account(db, agreement.offer.guest_id, action="sign this agreement")
    return _apply_signature(db, agreement, as_party, method=method, evidence_metadata=evidence_metadata)


def record_wet_ink_signature(
    db: Session, agreement: Agreement, as_party: str, scan_bytes: bytes, admin: AdminUser,
) -> Agreement:
    """ZR-ENG-CLR-004 AC-11 'System supports at least ... wet-ink fallback':
    the one alternate execution method actually buildable without a real
    e-signature/witness/notary provider behind it -- a scanned paper
    signature, uploaded as evidence. Admin-attested the same way
    sign_agreement is (a renter with a real Zoiko login must still sign
    in-app; this is for a walk-in/paper-only signer), and goes through the
    exact same required_signers/disclosure-gate/status-transition logic as
    every other signature via _apply_signature -- only the method and
    evidence file differ."""
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    if as_party == "renter":
        _assert_renter_has_no_account(db, agreement.offer.guest_id, action="sign this agreement")
    storage_ref, content_hash = save_agreement_document(scan_bytes)
    return _apply_signature(db, agreement, as_party, method="WET_INK", evidence_ref=storage_ref, evidence_hash=content_hash)


def list_signature_events(db: Session, agreement: Agreement) -> list[SignatureEvent]:
    return list(
        db.scalars(
            select(SignatureEvent).where(SignatureEvent.agreement_id == agreement.id).order_by(SignatureEvent.created_at)
        )
    )


def user_sign_agreement(
    db: Session, user: UserAccount, agreement: Agreement, *, method: str = "SIMPLE_ESIGN", evidence_metadata: dict | None = None,
) -> Agreement:
    """The renter signing their own agreement from their own session, instead
    of an admin attesting the signature on their behalf."""
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")
    if agreement.status not in ("SENT", "PARTIALLY_EXECUTED", "AMENDMENT_PENDING"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a sent agreement can be signed")
    if agreement.signed_by_renter_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "You have already signed this agreement")
    return _apply_signature(db, agreement, "renter", method=method, evidence_metadata=evidence_metadata)


def _generate_native_agreement_pdf(agreement: Agreement) -> bytes:
    """Mode A (native) rendering -- also reused, unmodified, as the
    'rendered content' side of mode C's own drift check against its
    authoritative reference text (see generate_agreement_pdf's mode C
    branch below). A plain summary document, not a legal contract template
    -- there's no real e-signature provider or clause library behind this,
    consistent with sign_agreement's own "simulated" framing.

    ZR-ENG-CLR-004 AC-27: reads only from the latest AgreementVersion's
    frozen snapshot (see _build_agreement_snapshot), never from live
    listing/offer/guest rows -- a later edit to any of those must never
    change what this renders for an already-generated version. Once a
    version reaches EXECUTED_IMMUTABLE this function must not be called
    again for it at all -- see freeze_agreement_version, which renders and
    hashes exactly once and persists the result; GET /agreements/{id}/pdf
    serves the persisted artifact thereafter, never a fresh render."""
    version = agreement.versions[-1]
    snapshot = version.snapshot

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4
    x = 20 * mm
    y = height - 25 * mm

    def write(text: str, size: float = 10, bold: bool = False, gap: float = 7 * mm) -> None:
        nonlocal y
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        pdf.drawString(x, y, text)
        y -= gap

    write("Zoiko Rooms -- Room Share Agreement", size=16, bold=True, gap=10 * mm)
    write(f"Agreement #{agreement.id}  |  Version {version.version_no}  |  Status: {agreement.status}", size=10)
    write(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", size=9, gap=10 * mm)

    write("Listing", size=12, bold=True)
    write(snapshot["listing_name"])
    write(f"{snapshot['listing_location']}, {snapshot['listing_city']}")
    if snapshot.get("room_size") is not None:
        write(
            f"Private room - {snapshot['room_size']} sqft - "
            f"{'Ensuite' if snapshot.get('room_has_ensuite') else 'Shared bathroom'}",
            gap=10 * mm,
        )
    else:
        y -= 10 * mm

    write("Provider", size=12, bold=True)
    write(snapshot["provider_name"])
    write(snapshot["provider_email"], gap=10 * mm)

    write("Renter", size=12, bold=True)
    write(snapshot["renter_name"])
    write(snapshot["renter_email"])
    if snapshot.get("renter_phone"):
        write(snapshot["renter_phone"])
    y -= 5 * mm

    write("Terms", size=12, bold=True)
    write(f"Monthly rent: Rs. {snapshot['monthly_rent']:,.2f}")
    write(f"Security deposit: Rs. {snapshot['deposit_amount']:,.2f}")
    write(f"Lease start: {snapshot['start_date']}")
    write(f"Term length: {snapshot['term_months']} months", gap=10 * mm)

    write("Signatures", size=12, bold=True)
    write(f"Provider: {agreement.signed_by_provider_at.strftime('%Y-%m-%d %H:%M UTC') if agreement.signed_by_provider_at else 'Not yet signed'}")
    write(f"Renter: {agreement.signed_by_renter_at.strftime('%Y-%m-%d %H:%M UTC') if agreement.signed_by_renter_at else 'Not yet signed'}")
    if agreement.signature_ref:
        write(f"Signature reference: {agreement.signature_ref}")

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()


def generate_agreement_pdf(db: Session, agreement: Agreement) -> bytes:
    """ZR-ENG-CLR-004 AC-04: dispatches on the version snapshot's own
    form_mode -- A renders natively (_generate_native_agreement_pdf); B
    overlays structured values onto the uploaded official form at its
    approved field anchors; C renders natively but is diff-checked against
    the template's counsel-approved reference text first (AC-04 'without
    converting it into an uncontrolled generic template'); D appends a
    signature-wrapper page to the uploaded external document. See
    services/agreement_form_rendering.py for the actual pypdf/reportlab
    work each of B/C/D does."""
    from app.models.agreement_form_template import AgreementFormTemplate
    from app.services.agreement_form_rendering import check_content_drift, render_mode_b_overlay, render_mode_d_document

    version = agreement.versions[-1]
    snapshot = version.snapshot
    form_mode = snapshot.get("form_mode", "A")

    if form_mode == "A":
        return _generate_native_agreement_pdf(agreement)

    template_id = snapshot.get("form_template_id")
    template = db.get(AgreementFormTemplate, template_id) if template_id else None
    if template is None:
        # The template that generated this version was later retired/
        # deleted from under it -- fail closed rather than silently fall
        # back to native rendering, which would misrepresent what this
        # version was actually generated as.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"No form template available to render mode {form_mode} for this version",
        )

    if form_mode == "C":
        rendered_text = generate_agreement_accessible_text(agreement)
        if check_content_drift(rendered_text, template.authoritative_content_text):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Generated content has drifted from the authoritative prescribed-content reference -- blocked, not delivered",
            )
        return _generate_native_agreement_pdf(agreement)

    source_bytes = resolve_agreement_document_path(template.source_document_storage_ref).read_bytes()
    if form_mode == "B":
        return render_mode_b_overlay(source_bytes, template.field_anchor_map, snapshot)
    if form_mode == "D":
        return render_mode_d_document(source_bytes, snapshot, agreement.status)

    # form_mode "E" (or anything unrecognized) is never reachable here --
    # resolve_agreement_profile fails closed for E before an agreement (and
    # therefore a version/snapshot) can exist at all.
    raise HTTPException(status.HTTP_409_CONFLICT, f"Unsupported form_mode {form_mode!r} for rendering")


def generate_agreement_accessible_text(agreement: Agreement) -> str:
    """ZR-ENG-CLR-004 AC-28 'Accessibility and alternate execution/delivery
    paths are supported where required' / Q-35 'Renter uses screen reader/
    alternate accessible document flow': a plain-text rendering of the same
    frozen snapshot generate_agreement_pdf uses -- same AC-27 rule (reads
    only the version's snapshot, never live listing/offer rows), just a
    linear-reading-order text format instead of a paginated PDF, for screen
    readers and any client that can't render PDF at all."""
    version = agreement.versions[-1]
    snapshot = version.snapshot

    lines = [
        "ZOIKO ROOMS -- ROOM SHARE AGREEMENT (accessible text version)",
        f"Agreement #{agreement.id}, Version {version.version_no}, Status: {agreement.status}",
        "",
        "LISTING",
        snapshot["listing_name"],
        f"{snapshot['listing_location']}, {snapshot['listing_city']}",
        "",
        "PROVIDER",
        snapshot["provider_name"],
        snapshot["provider_email"],
        "",
        "RENTER",
        snapshot["renter_name"],
        snapshot["renter_email"],
        "",
        "TERMS",
        f"Monthly rent: Rs. {snapshot['monthly_rent']:,.2f}",
        f"Security deposit: Rs. {snapshot['deposit_amount']:,.2f}",
        f"Lease start: {snapshot['start_date']}",
        f"Term length: {snapshot['term_months']} months",
        "",
        "SIGNATURES",
        f"Provider: {'signed ' + agreement.signed_by_provider_at.strftime('%Y-%m-%d %H:%M UTC') if agreement.signed_by_provider_at else 'not yet signed'}",
        f"Renter: {'signed ' + agreement.signed_by_renter_at.strftime('%Y-%m-%d %H:%M UTC') if agreement.signed_by_renter_at else 'not yet signed'}",
    ]
    return "\n".join(lines)


def generate_disclosure_accessible_text(disclosure: DisclosureRequirement) -> str:
    """AC-28 counterpart for one disclosure -- same placeholder-content
    status as _generate_disclosure_document's PDF (see that function's own
    docstring)."""
    return "\n".join([
        disclosure.title or disclosure.disclosure_type,
        "",
        "Internal placeholder disclosure content -- not counsel-approved legal wording.",
        "See ZR-ENG-CLR-004 LEGAL CONTROL doctrine.",
    ])


def freeze_agreement_version(db: Session, agreement: Agreement) -> DocumentArtifact:
    """ZR-ENG-CLR-004 Section 9.2/AC-07/AC-08: renders the PDF exactly once
    for the agreement's current version, hashes it, and persists it as an
    immutable DocumentArtifact -- called once, from
    confirm_agreement_payment, the moment the agreement reaches its terminal
    SIGNED state. Idempotent: if this version already has an artifact
    (e.g. a retried call), the existing one is returned unchanged rather than
    re-rendering (content-addressed, no storage replacement under the same
    artifact id -- Section 14.3).

    Section 14.3/Q-29 'two final signatures arrive simultaneously; one
    executed artifact only': the `if version.artifact is not None` check
    above is a fast-path only, not the real guarantee -- two concurrent
    callers can both pass it before either commits. DocumentArtifact.
    agreement_version_id is DB-unique, so only one insert can ever win; the
    loser catches IntegrityError and returns the winner's row instead of
    crashing. Same pattern as services/inventory.py:create_hold."""
    version = agreement.versions[-1]
    if version.artifact is not None:
        return version.artifact

    pdf_bytes = generate_agreement_pdf(db, agreement)
    storage_ref, content_hash = save_agreement_document(pdf_bytes)

    # The insert must happen inside the SAVEPOINT -- entering begin_nested()
    # flushes whatever's already pending into the *outer* transaction first,
    # so the version.status/content_hash/frozen_at writes below are only
    # protected by the nested rollback if made inside this block.
    try:
        with db.begin_nested():
            version.status = "EXECUTED_IMMUTABLE"
            version.content_hash = content_hash
            version.frozen_at = datetime.now(timezone.utc)
            artifact = DocumentArtifact(
                agreement_version_id=version.id, content_hash=content_hash, storage_ref=storage_ref,
            )
            db.add(artifact)
            # Section 7.1/13.1 execution_certificate: the structured evidence
            # roll-up, generated alongside the PDF for the same version --
            # covered by the same SAVEPOINT/race guard as the artifact above.
            db.add(ExecutionCertificate(
                agreement_version_id=version.id, document_hash=content_hash,
                signer_summary=[
                    {
                        "role": e.signer_role, "identifier": e.signer_identifier, "method": e.method,
                        "consentedAt": e.consented_at.isoformat(),
                    }
                    for e in version.signature_events
                ],
                provider_transaction_ids=[
                    sr.provider_transaction_id for sr in db.scalars(
                        select(SignatureRequest).where(SignatureRequest.agreement_version_id == version.id)
                    ) if sr.provider_transaction_id
                ],
            ))
            db.flush()
    except IntegrityError:
        db.refresh(version)
        return version.artifact

    db.commit()
    db.refresh(artifact)

    # ZR-ENG-CLR-004 AC-18/Section 9.4: if this version is what an amendment
    # was generated to be signed, its own EXECUTED/EFFECTIVE moments happen
    # here, not as a separate admin action -- the spec's own diagram draws
    # EXECUTED -> EFFECTIVE as a direct, unconditional arrow (see
    # models/agreement_amendment.py's own docstring on that collapsing
    # convention).
    pending_amendment = db.scalar(
        select(AgreementAmendment).where(
            AgreementAmendment.resulting_version_id == version.id, AgreementAmendment.status == "EXECUTION_PENDING",
        )
    )
    if pending_amendment is not None:
        now = datetime.now(timezone.utc)
        pending_amendment.status = "EFFECTIVE"
        pending_amendment.executed_at = now
        pending_amendment.effective_at = now
        _supersede_payment_schedule_if_rent_changed(db, agreement, pending_amendment)
        db.commit()

    return artifact


def _supersede_payment_schedule_if_rent_changed(db: Session, agreement: Agreement, amendment: AgreementAmendment) -> None:
    """ZR-ENG-CLR-005 Section 7.2: "Future obligations can be superseded by a
    new schedule version once the contractual change becomes effective."
    No-op for the common case (an amendment that doesn't touch rent -- e.g.
    deposit-only, term-length-only, corrections). Only ever creates a new
    PaymentSchedule row and flips the old one's status -- no existing
    Obligation (paid or unpaid) is touched, so "already-paid obligations are
    never silently rewritten" holds by construction. The next
    generate_next_rent_obligation call picks up the new row automatically."""
    if "monthlyRent" not in amendment.proposed_terms:
        return

    current = db.scalar(
        select(PaymentSchedule).where(PaymentSchedule.agreement_id == agreement.id, PaymentSchedule.status == "ACTIVE")
    )
    if current is None:
        return  # defensive -- create_agreement always creates one, so this shouldn't happen

    current.status = "SUPERSEDED"
    db.add(PaymentSchedule(
        agreement_id=agreement.id,
        version=current.version + 1,
        cadence=current.cadence,
        currency=current.currency,
        amount=float(amendment.proposed_terms["monthlyRent"]),
        first_due=date_.today(),
        anchor_day=current.anchor_day,
    ))
