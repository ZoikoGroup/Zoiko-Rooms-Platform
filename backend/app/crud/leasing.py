from datetime import date as date_, datetime, timezone
from io import BytesIO

from fastapi import HTTPException, status
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.mailer import send_agreement_executed_email, send_application_decided_email
from app.crud.eligibility import check_agreement_eligibility, check_offer_eligibility
from app.crud.guest import get_guest_for_user, get_user_for_guest
from app.crud.ids import dicebear_avatar, new_id
from app.crud.listing import is_listing_available
from app.crud import notification as notif_crud
from app.crud.occupancy import _add_months
from app.crud.party import assert_provider_access, party_id_for_listing
from app.crud.user import get_user_by_party_id
from app.models.admin_user import AdminUser
from app.models.finance import OBLIGATION_TYPE_TO_PLANE, Obligation
from app.models.guest import Guest
from app.models.leasing import Agreement, Application, ApplicationDecision, Offer, OfferTerms
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.user_account import UserAccount
from app.schemas.leasing import ApplicationCreate, ApplicationDecide, ApplicationRead, ApplicationUpdate, OfferTermsCreate


def to_application_read(application: Application) -> ApplicationRead:
    return ApplicationRead(
        id=application.id,
        listing_id=application.listing_id,
        listing_name=application.listing.name if application.listing else "",
        guest_id=application.guest_id,
        guest_name=application.guest.name,
        guest_email=application.guest.email,
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
    application = Application(
        listing_id=listing.id,
        guest_id=guest.id,
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


def get_offer_or_404(db: Session, offer_id: int) -> Offer:
    offer = db.get(Offer, offer_id)
    if not offer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Offer not found")
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


def add_offer_terms(db: Session, offer: Offer, admin: AdminUser, data: OfferTermsCreate) -> OfferTerms:
    assert_provider_access(db, admin, party_id_for_listing(offer.listing))
    if offer.status not in ("DRAFT", "SENT"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Offer terms can only be added while the offer is draft or sent")

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
    db.commit()
    db.refresh(terms)
    return terms


def set_offer_status(db: Session, offer: Offer, admin: AdminUser, new_status: str) -> Offer:
    assert_provider_access(db, admin, party_id_for_listing(offer.listing))
    if new_status in ("ACCEPTED", "DECLINED"):
        _assert_renter_has_no_account(db, offer.guest_id, action="accept or decline this offer")
    offer.status = new_status

    guest = db.get(Guest, offer.guest_id)
    listing = offer.listing
    if new_status == "SENT":
        if guest:
            notif_crud.notify_user_by_guest(
                db, guest,
                title="You have a new rental offer",
                message=f'You have a new offer for "{listing.name}". Review it and let your host know.',
                notification_type="offer.sent",
                related_entity_type="offer", related_entity_id=str(offer.id),
            )
    elif new_status == "ACCEPTED":
        # The authoritative "accepted" state for this workflow -- both sides are
        # told only once it's actually committed here, never earlier.
        if guest:
            notif_crud.notify_user_by_guest(
                db, guest,
                title="Your offer was accepted",
                message=f'Your offer for "{listing.name}" has been accepted.',
                notification_type="offer.accepted",
                related_entity_type="offer", related_entity_id=str(offer.id),
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

    db.commit()
    db.refresh(offer)
    if new_status == "SENT":
        _notify_offer_guest(db, offer, title="You have a new rental offer", notification_type="offer.sent")
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


def user_accept_offer(db: Session, user: UserAccount, offer: Offer) -> Offer:
    """The renter accepting their own offer -- previously only an admin could
    do this on the renter's behalf, with no real consent captured from the
    renter's own session."""
    _guest_owns_offer(db, user, offer)
    if offer.status != "SENT":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a sent offer can be accepted")
    offer.status = "ACCEPTED"
    db.commit()
    db.refresh(offer)
    return offer


def user_decline_offer(db: Session, user: UserAccount, offer: Offer) -> Offer:
    _guest_owns_offer(db, user, offer)
    if offer.status != "SENT":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a sent offer can be declined")
    offer.status = "DECLINED"
    db.commit()
    db.refresh(offer)
    return offer


def create_agreement(db: Session, offer: Offer, admin: AdminUser) -> Agreement:
    assert_provider_access(db, admin, party_id_for_listing(offer.listing))
    reasons = check_agreement_eligibility(db, offer)
    if reasons:
        raise HTTPException(status.HTTP_409_CONFLICT, {"message": "Not eligible to create an agreement", "reasons": reasons})

    agreement = Agreement(offer_id=offer.id)
    db.add(agreement)
    db.flush()

    latest_terms = offer.terms[-1]
    db.add(
        Obligation(
            obligation_type="RENT",
            money_plane=OBLIGATION_TYPE_TO_PLANE["RENT"],
            amount=latest_terms.monthly_rent,
            due_date=latest_terms.start_date,
            agreement_id=agreement.id,
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
    db.commit()
    db.refresh(agreement)
    return agreement


def get_agreement_or_404(db: Session, agreement_id: int) -> Agreement:
    agreement = db.get(Agreement, agreement_id)
    if not agreement:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agreement not found")
    return agreement


def send_agreement(db: Session, agreement: Agreement, admin: AdminUser) -> Agreement:
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    agreement.status = "SENT"

    guest = db.get(Guest, agreement.offer.guest_id)
    if guest:
        notif_crud.notify_user_by_guest(
            db, guest,
            title="Your rental agreement is ready to sign",
            message=f'The agreement for "{agreement.offer.listing.name}" requires your signature.',
            notification_type="agreement.sent",
            related_entity_type="agreement", related_entity_id=str(agreement.id),
        )

    db.commit()
    db.refresh(agreement)
    _notify_offer_guest(
        db, agreement.offer,
        title="Your rental agreement is ready to sign",
        notification_type="agreement.sent",
    )
    return agreement


def _apply_signature(db: Session, agreement: Agreement, as_party: str) -> Agreement:
    """Simulated e-signature -- no real DocuSign-style provider is connected. Records
    a signature token and timestamp per party; the agreement is SIGNED once both
    sides have signed."""
    if as_party not in ("provider", "renter"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "asParty must be 'provider' or 'renter'")

    now = datetime.now(timezone.utc)
    if as_party == "provider":
        agreement.signed_by_provider_at = now
    else:
        agreement.signed_by_renter_at = now
    if not agreement.signature_ref:
        agreement.signature_ref = new_id("SIG")

    if agreement.signed_by_provider_at and agreement.signed_by_renter_at:
        agreement.status = "SIGNED"

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

        # Occupancy is committed to this offer as soon as the lease is executed --
        # move-in itself happens later via confirm_move_in. Guarded against
        # duplicate creation since a party can be re-signed for (see sign_agreement).
        offer = agreement.offer
        existing_occupancy = db.scalar(select(Occupancy).where(Occupancy.offer_id == offer.id))
        if not existing_occupancy:
            latest_terms = offer.terms[-1]
            db.add(
                Occupancy(
                    offer_id=offer.id,
                    listing_id=offer.listing_id,
                    room_id=offer.listing.room_id,
                    guest_id=offer.guest_id,
                    status="PENDING_MOVE_IN",
                    expected_end_date=_add_months(latest_terms.start_date, latest_terms.term_months),
                )
            )

    db.commit()
    db.refresh(agreement)
    if agreement.status == "SIGNED":
        _notify_offer_guest(
            db, agreement.offer,
            title="Your rental agreement is fully signed",
            notification_type="agreement.signed",
        )
    return agreement


def sign_agreement(db: Session, agreement: Agreement, as_party: str, admin: AdminUser) -> Agreement:
    """Admin-attested signature -- for a renter who has no Zoiko login of their
    own (a walk-in guest the provider is onboarding manually), the admin
    records that a signature was collected out of band. A renter with a real
    account can't be signed for this way -- enforced below, not just documented
    -- they must sign themselves via user_sign_agreement instead."""
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    if as_party == "renter":
        _assert_renter_has_no_account(db, agreement.offer.guest_id, action="sign this agreement")
    return _apply_signature(db, agreement, as_party)


def user_sign_agreement(db: Session, user: UserAccount, agreement: Agreement) -> Agreement:
    """The renter signing their own agreement from their own session, instead
    of an admin attesting the signature on their behalf."""
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")
    if agreement.status not in ("SENT",):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a sent agreement can be signed")
    if agreement.signed_by_renter_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "You have already signed this agreement")
    return _apply_signature(db, agreement, "renter")


def generate_agreement_pdf(agreement: Agreement) -> bytes:
    """A plain summary document, not a legal contract template -- there's no
    real e-signature provider or clause library behind this, consistent with
    sign_agreement's own "simulated" framing."""
    offer = agreement.offer
    listing = offer.listing
    room = listing.room
    guest = offer.guest
    latest_terms = offer.terms[-1] if offer.terms else None

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
    write(f"Agreement #{agreement.id}  |  Version {agreement.version}  |  Status: {agreement.status}", size=10)
    write(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", size=9, gap=10 * mm)

    write("Listing", size=12, bold=True)
    write(listing.name)
    write(f"{listing.location}, {listing.city}")
    if room:
        write(f"Private room - {room.size} sqft - {'Ensuite' if room.has_ensuite else 'Shared bathroom'}", gap=10 * mm)
    else:
        y -= 10 * mm

    write("Provider", size=12, bold=True)
    write(listing.owner.full_name)
    write(listing.owner.email, gap=10 * mm)

    write("Renter", size=12, bold=True)
    write(guest.name)
    write(guest.email)
    if guest.phone:
        write(guest.phone)
    y -= 5 * mm

    write("Terms", size=12, bold=True)
    if latest_terms:
        write(f"Monthly rent: Rs. {latest_terms.monthly_rent:,.2f}")
        write(f"Security deposit: Rs. {latest_terms.deposit_amount:,.2f}")
        write(f"Lease start: {latest_terms.start_date.isoformat()}")
        write(f"Term length: {latest_terms.term_months} months", gap=10 * mm)
    else:
        write("No terms recorded", gap=10 * mm)

    write("Signatures", size=12, bold=True)
    write(f"Provider: {agreement.signed_by_provider_at.strftime('%Y-%m-%d %H:%M UTC') if agreement.signed_by_provider_at else 'Not yet signed'}")
    write(f"Renter: {agreement.signed_by_renter_at.strftime('%Y-%m-%d %H:%M UTC') if agreement.signed_by_renter_at else 'Not yet signed'}")
    if agreement.signature_ref:
        write(f"Signature reference: {agreement.signature_ref}")

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()
