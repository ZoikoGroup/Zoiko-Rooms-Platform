"""ZR-ENG-CLR-008 Section 8 MVP slice: renter-initiated DATE_SHIFT (move-in
date, before the renter has moved in) and EXTENSION (move-out date pushed
later, after move-in). See models/booking_change_request.py for why these
are mutually exclusive states of the same agreement.

Approval drives the existing AgreementAmendment engine
(crud/agreement_amendments.py) for the actual contract re-papering step --
this module supplies the renter-initiated request/decision trail that engine
has no room for on its own (it is admin-only and starts already-classified).
A request only reaches BOOKING_CHANGE_STATUSES' "EFFECTIVE" once the
resulting amendment is fully re-signed (see crud/leasing.py's post-signature
amendment hook) -- "APPROVED" here means the host/admin decision only.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import agreement_amendments as amendment_crud
from app.crud import notification as notif_crud
from app.crud.guest import get_guest_for_user
from app.crud.market_policy import resolve_market_policy
from app.crud.occupancy import _add_months
from app.crud.party import assert_provider_access, party_id_for_listing
from app.models.admin_user import AdminUser
from app.models.booking_change_request import BookingChangeRequest
from app.models.leasing import Agreement, Offer
from app.models.occupancy import Occupancy
from app.models.user_account import UserAccount
from app.schemas.leasing import BookingChangeRequestRead

REQUEST_EXPIRY = timedelta(days=7)


def to_booking_change_request_read(bcr: BookingChangeRequest) -> BookingChangeRequestRead:
    agreement = bcr.agreement
    listing = agreement.offer.listing if agreement and agreement.offer else None
    guest = bcr.requested_by
    return BookingChangeRequestRead(
        id=bcr.id,
        agreement_id=bcr.agreement_id,
        requested_by_guest_id=bcr.requested_by_guest_id,
        change_type=bcr.change_type,
        status=bcr.status,
        original_start_date=bcr.original_start_date,
        proposed_start_date=bcr.proposed_start_date,
        original_end_date=bcr.original_end_date,
        proposed_end_date=bcr.proposed_end_date,
        additional_term_months=bcr.additional_term_months,
        target_listing_id=bcr.target_listing_id,
        resulting_application_id=bcr.resulting_application_id,
        original_monthly_rent=float(bcr.original_monthly_rent) if bcr.original_monthly_rent is not None else None,
        proposed_monthly_rent=float(bcr.proposed_monthly_rent) if bcr.proposed_monthly_rent is not None else None,
        reason=bcr.reason,
        decision_note=bcr.decision_note,
        decided_by_admin_id=bcr.decided_by_admin_id,
        decided_at=bcr.decided_at,
        resulting_amendment_id=bcr.resulting_amendment_id,
        created_at=bcr.created_at,
        expires_at=bcr.expires_at,
        listing_name=listing.name if listing else "",
        target_listing_name=bcr.target_listing.name if bcr.target_listing else "",
        guest_name=guest.name if guest else "",
    )


def get_booking_change_request_or_404(db: Session, bcr_id: int) -> BookingChangeRequest:
    bcr = db.get(BookingChangeRequest, bcr_id)
    if not bcr:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking change request not found")
    return bcr


def _current_start_date(offer: Offer) -> date:
    return offer.terms[-1].start_date


def _change_label(change_type: str) -> str:
    return {
        "EXTENSION": "stay extension", "SHORTENING": "stay shortening", "PREMISES_CHANGE": "room/property change",
        "FINANCIAL_CHANGE": "rent change",
    }.get(change_type, "move-in date change")


def _expire_if_overdue(db: Session, bcr: BookingChangeRequest) -> BookingChangeRequest:
    if bcr.status == "PENDING" and bcr.expires_at <= datetime.now(timezone.utc):
        bcr.status = "EXPIRED"
        db.commit()
        db.refresh(bcr)
    return bcr


def request_date_change(
    db: Session, user: UserAccount, agreement: Agreement, proposed_start_date: date, reason: str = "",
) -> BookingChangeRequest:
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")

    if agreement.status != "SIGNED":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only a fully signed agreement's move-in date can be changed this way",
        )

    offer = agreement.offer
    already_moved_in = db.scalar(select(Occupancy).where(Occupancy.offer_id == offer.id))
    if already_moved_in is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You have already moved in -- this move-in date can no longer be changed this way",
        )

    open_request = db.scalar(
        select(BookingChangeRequest).where(
            BookingChangeRequest.agreement_id == agreement.id, BookingChangeRequest.status == "PENDING",
        )
    )
    if open_request is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A change request is already pending for this agreement")

    current_start = _current_start_date(offer)
    if proposed_start_date == current_start:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Proposed move-in date is the same as the current one")
    if proposed_start_date < date.today():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Proposed move-in date cannot be in the past")

    bcr = BookingChangeRequest(
        agreement_id=agreement.id, requested_by_guest_id=guest.id, change_type="DATE_SHIFT", status="PENDING",
        original_start_date=current_start, proposed_start_date=proposed_start_date, reason=reason,
        expires_at=datetime.now(timezone.utc) + REQUEST_EXPIRY,
    )
    db.add(bcr)
    db.commit()
    db.refresh(bcr)

    listing = offer.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Move-in date change requested",
            message=f'A tenant has requested to change their move-in date for "{listing.name}".',
            notification_type="booking_change_request.submitted",
            related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
        )
    return bcr


def request_extension(
    db: Session, user: UserAccount, agreement: Agreement, additional_term_months: int, reason: str = "",
) -> BookingChangeRequest:
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")

    if agreement.status != "SIGNED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a fully signed agreement can be extended")

    offer = agreement.offer
    occupancy = db.scalar(select(Occupancy).where(Occupancy.offer_id == offer.id))
    if occupancy is None or occupancy.status != "ACTIVE":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An extension can only be requested once you've moved in and are an active tenant",
        )

    open_request = db.scalar(
        select(BookingChangeRequest).where(
            BookingChangeRequest.agreement_id == agreement.id, BookingChangeRequest.status == "PENDING",
        )
    )
    if open_request is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A change request is already pending for this agreement")

    current_start = _current_start_date(offer)
    current_term_months = offer.terms[-1].term_months
    new_term_months = current_term_months + additional_term_months
    proposed_end_date = _add_months(current_start, new_term_months)

    bcr = BookingChangeRequest(
        agreement_id=agreement.id, requested_by_guest_id=guest.id, change_type="EXTENSION", status="PENDING",
        original_start_date=current_start, proposed_start_date=current_start,
        original_end_date=occupancy.expected_end_date, proposed_end_date=proposed_end_date,
        additional_term_months=additional_term_months, reason=reason,
        expires_at=datetime.now(timezone.utc) + REQUEST_EXPIRY,
    )
    db.add(bcr)
    db.commit()
    db.refresh(bcr)

    listing = offer.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Stay extension requested",
            message=f'A tenant has requested to extend their stay at "{listing.name}".',
            notification_type="booking_change_request.submitted",
            related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
        )
    return bcr


def request_shortening(
    db: Session, user: UserAccount, agreement: Agreement, reduced_term_months: int, reason: str = "",
) -> BookingChangeRequest:
    """ZR-ENG-CLR-008 Section 7.4/AC-10: shortening is only a valid amendment
    *before* move-in. Once an Occupancy exists, an early end routes to
    Section 6 termination (crud/occupancy.py:end_occupancy) instead -- this
    function refuses rather than silently reimplementing that routing or
    disguising termination economics as a harmless date edit."""
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")

    if agreement.status != "SIGNED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a fully signed agreement's term can be shortened this way")

    offer = agreement.offer
    already_moved_in = db.scalar(select(Occupancy).where(Occupancy.offer_id == offer.id))
    if already_moved_in is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You have already moved in -- an early end now goes through ending your tenancy, not a date-shortening request",
        )

    open_request = db.scalar(
        select(BookingChangeRequest).where(
            BookingChangeRequest.agreement_id == agreement.id, BookingChangeRequest.status == "PENDING",
        )
    )
    if open_request is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A change request is already pending for this agreement")

    current_start = _current_start_date(offer)
    current_term_months = offer.terms[-1].term_months
    new_term_months = current_term_months - reduced_term_months
    # AC-11: a zero-or-negative-length stay is a cancellation, not an
    # amendment -- not something this endpoint can route to on its own.
    if new_term_months < 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That reduction would leave no term remaining -- withdraw the booking through cancellation instead",
        )

    bcr = BookingChangeRequest(
        agreement_id=agreement.id, requested_by_guest_id=guest.id, change_type="SHORTENING", status="PENDING",
        original_start_date=current_start, proposed_start_date=current_start,
        original_end_date=_add_months(current_start, current_term_months),
        proposed_end_date=_add_months(current_start, new_term_months),
        additional_term_months=-reduced_term_months, reason=reason,
        expires_at=datetime.now(timezone.utc) + REQUEST_EXPIRY,
    )
    db.add(bcr)
    db.commit()
    db.refresh(bcr)

    listing = offer.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Stay shortening requested",
            message=f'A tenant has requested to shorten their stay at "{listing.name}" before moving in.',
            notification_type="booking_change_request.submitted",
            related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
        )
    return bcr


def request_premises_change(
    db: Session, user: UserAccount, agreement: Agreement, target_listing_id: str, reason: str = "",
) -> BookingChangeRequest:
    """ZR-ENG-CLR-008 Section 14: 'A different room or property changes the
    subject matter of the booking and usually requires a new booking/version
    family.' Available both before and after move-in (unlike DATE_SHIFT/
    SHORTENING) -- a room swap is a legitimate ask at either stage, and the
    replacement flow (approve_change_request's PREMISES_CHANGE branch) opens
    a genuinely fresh Application rather than mutating anything in place."""
    from app.models.listing import Listing

    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")

    if agreement.status != "SIGNED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a fully signed agreement can request a room/property change")

    if target_listing_id == agreement.offer.listing_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That is already your current listing")

    target_listing = db.get(Listing, target_listing_id)
    if not target_listing or target_listing.state != "PUBLISHED":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target listing not found or not currently published")

    open_request = db.scalar(
        select(BookingChangeRequest).where(
            BookingChangeRequest.agreement_id == agreement.id, BookingChangeRequest.status == "PENDING",
        )
    )
    if open_request is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A change request is already pending for this agreement")

    offer = agreement.offer
    current_start = _current_start_date(offer)
    bcr = BookingChangeRequest(
        agreement_id=agreement.id, requested_by_guest_id=guest.id, change_type="PREMISES_CHANGE", status="PENDING",
        original_start_date=current_start, proposed_start_date=current_start,
        target_listing_id=target_listing_id, reason=reason,
        expires_at=datetime.now(timezone.utc) + REQUEST_EXPIRY,
    )
    db.add(bcr)
    db.commit()
    db.refresh(bcr)

    listing = offer.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Room/property change requested",
            message=f'A tenant is asking to move from "{listing.name}" to a different listing.',
            notification_type="booking_change_request.submitted",
            related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
        )
    return bcr


def request_financial_change(
    db: Session, user: UserAccount, agreement: Agreement, proposed_monthly_rent: float, reason: str = "",
) -> BookingChangeRequest:
    """ZR-ENG-CLR-008 Section 10/AC-24: renter requesting a new monthly rent.
    Gated by MarketPolicyPack.rent_change_min_interval_days -- both the doc's
    own NSW ('once in 12 months') and Ontario rent-guideline examples cite a
    minimum interval, not a blanket ban, so this checks the last EFFECTIVE
    financial change on this agreement rather than refusing every repeat."""
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")

    if agreement.status != "SIGNED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a fully signed agreement's rent can be changed this way")

    offer = agreement.offer
    current_start = _current_start_date(offer)
    current_rent = float(offer.terms[-1].monthly_rent)
    if proposed_monthly_rent == current_rent:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Proposed rent is the same as the current rent")

    open_request = db.scalar(
        select(BookingChangeRequest).where(
            BookingChangeRequest.agreement_id == agreement.id, BookingChangeRequest.status == "PENDING",
        )
    )
    if open_request is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A change request is already pending for this agreement")

    policy = resolve_market_policy(db)
    last_effective = db.scalar(
        select(BookingChangeRequest)
        .where(
            BookingChangeRequest.agreement_id == agreement.id,
            BookingChangeRequest.change_type == "FINANCIAL_CHANGE",
            BookingChangeRequest.status == "EFFECTIVE",
        )
        .order_by(BookingChangeRequest.decided_at.desc())
    )
    if last_effective is not None and last_effective.decided_at is not None:
        days_since = (datetime.now(timezone.utc) - last_effective.decided_at).days
        if days_since < policy.rent_change_min_interval_days:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Rent was already changed {days_since} day(s) ago -- the resolved policy requires at least "
                f"{policy.rent_change_min_interval_days} days between rent changes (jurisdiction={policy.jurisdiction_code})",
            )

    bcr = BookingChangeRequest(
        agreement_id=agreement.id, requested_by_guest_id=guest.id, change_type="FINANCIAL_CHANGE", status="PENDING",
        original_start_date=current_start, proposed_start_date=current_start,
        original_monthly_rent=current_rent, proposed_monthly_rent=proposed_monthly_rent, reason=reason,
        expires_at=datetime.now(timezone.utc) + REQUEST_EXPIRY,
    )
    db.add(bcr)
    db.commit()
    db.refresh(bcr)

    listing = offer.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Rent change requested",
            message=f'A tenant has requested a new monthly rent for "{listing.name}".',
            notification_type="booking_change_request.submitted",
            related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
        )
    return bcr


def _complete_premises_change_if_applicable(db: Session, new_agreement: Agreement) -> None:
    """Called from crud/leasing.py right after ANY agreement reaches SIGNED --
    a no-op unless that agreement's Application is the replacement side of a
    PREMISES_CHANGE migration (see request_premises_change /
    approve_change_request's PREMISES_CHANGE branch). This is the actual
    'replacement commit policy permits release' moment (Section 8 Edge
    Cases: 'preserve old booking until replacement commit policy permits
    release') -- the original booking is only ended/voided now, never at
    BCR-approval time, so a renter who never finishes signing/paying for the
    new listing keeps their original tenancy untouched."""
    from app.crud.occupancy import end_occupancy

    if new_agreement.offer is None:
        return
    bcr = db.scalar(
        select(BookingChangeRequest).where(
            BookingChangeRequest.resulting_application_id == new_agreement.offer.application_id,
            BookingChangeRequest.change_type == "PREMISES_CHANGE",
            BookingChangeRequest.status == "APPROVED",
        )
    )
    if bcr is None:
        return

    old_agreement = bcr.agreement
    old_occupancy = db.scalar(select(Occupancy).where(Occupancy.offer_id == old_agreement.offer_id))
    deciding_admin = db.get(AdminUser, bcr.decided_by_admin_id) if bcr.decided_by_admin_id else None
    if old_occupancy is not None and old_occupancy.status == "ACTIVE" and deciding_admin is not None:
        end_occupancy(db, old_occupancy, deciding_admin, basis="PREMISES_CHANGE_MIGRATION")
    elif old_agreement.status not in ("VOID", "EXPIRED"):
        old_agreement.status = "VOID"

    bcr.status = "EFFECTIVE"
    db.commit()

    notif_crud.notify_user_by_guest(
        db, bcr.requested_by,
        title="Your room/property change is now effective",
        message="Your new agreement is fully signed and your original booking has been closed out.",
        notification_type="booking_change_request.effective",
        related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
    )


def _approve_premises_change(db: Session, bcr: BookingChangeRequest, admin: AdminUser, decision_note: str) -> BookingChangeRequest:
    """No amendment engine involved -- a different room/property is a
    different subject matter (Section 14), so this opens a genuinely fresh
    Application (auto-approved, since the admin decision already happened
    right here) on the target listing and lets the ordinary leasing pipeline
    (offer terms -> accept -> agreement -> sign -> pay) run its own real
    compliance/pricing checks for that listing. The original booking stays
    fully intact until that new agreement reaches SIGNED (see
    _complete_premises_change_if_applicable) -- APPROVED here only means
    'the host agreed to let this renter apply', not that anything has moved."""
    from app.crud.leasing import decide_application, submit_application
    from app.models.listing import Listing
    from app.schemas.leasing import ApplicationCreate, ApplicationDecide

    agreement = bcr.agreement
    target_listing = db.get(Listing, bcr.target_listing_id)
    if not target_listing or target_listing.state != "PUBLISHED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Target listing is no longer published")

    application = submit_application(db, ApplicationCreate(
        listing_id=target_listing.id, guest_id=bcr.requested_by_guest_id,
        message=f"Room/property change migration from listing {agreement.offer.listing_id} (request #{bcr.id})",
    ))
    decide_application(db, application, admin, ApplicationDecide(
        decision="APPROVED", reason_code="PREMISES_CHANGE_MIGRATION",
        note=decision_note or "Pre-approved: renter's existing tenancy is being migrated to this listing.",
    ))

    bcr.status = "APPROVED"
    bcr.decided_by_admin_id = admin.id
    bcr.decided_at = datetime.now(timezone.utc)
    bcr.decision_note = decision_note
    bcr.resulting_application_id = application.id
    db.commit()
    db.refresh(bcr)

    notif_crud.notify_user_by_guest(
        db, bcr.requested_by,
        title="Your room/property change was approved",
        message=(
            f'You can now proceed on "{target_listing.name}" -- your host will send offer terms next. '
            "Your current tenancy stays active until that new agreement is fully signed."
        ),
        notification_type="booking_change_request.approved",
        related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
    )
    return bcr


def approve_change_request(db: Session, bcr: BookingChangeRequest, admin: AdminUser, decision_note: str = "") -> BookingChangeRequest:
    agreement = bcr.agreement
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    bcr = _expire_if_overdue(db, bcr)
    if bcr.status != "PENDING":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only a PENDING request can be approved (current status: {bcr.status})")

    if bcr.change_type == "PREMISES_CHANGE":
        return _approve_premises_change(db, bcr, admin, decision_note)

    if bcr.change_type in ("EXTENSION", "SHORTENING"):
        # additional_term_months is a signed delta (negative for SHORTENING),
        # so this one line correctly covers both directions.
        current_term_months = agreement.offer.terms[-1].term_months
        proposed_terms = {"termMonths": current_term_months + bcr.additional_term_months}
        default_reason = "Renter-requested stay extension" if bcr.change_type == "EXTENSION" else "Renter-requested stay shortening"
    elif bcr.change_type == "FINANCIAL_CHANGE":
        proposed_terms = {"monthlyRent": float(bcr.proposed_monthly_rent)}
        default_reason = "Renter-requested rent change"
    else:
        proposed_terms = {"startDate": bcr.proposed_start_date.isoformat()}
        default_reason = "Renter-requested move-in date change"

    amendment = amendment_crud.request_amendment(db, agreement, admin, reason=bcr.reason or default_reason)
    amendment = amendment_crud.classify_amendment(db, amendment, admin, "MATERIAL_CHANGE")
    amendment = amendment_crud.propose_terms(db, amendment, admin, proposed_terms)
    amendment = amendment_crud.approve_amendment(db, amendment, admin)

    bcr.status = "APPROVED"
    bcr.decided_by_admin_id = admin.id
    bcr.decided_at = datetime.now(timezone.utc)
    bcr.decision_note = decision_note
    bcr.resulting_amendment_id = amendment.id
    db.commit()
    db.refresh(bcr)

    change_label = _change_label(bcr.change_type)
    notif_crud.notify_user_by_guest(
        db, bcr.requested_by,
        title=f"Your {change_label} was approved",
        message=f"Your requested {change_label} was approved -- please review and re-sign the updated agreement.",
        notification_type="booking_change_request.approved",
        related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
    )
    return bcr


def decline_change_request(db: Session, bcr: BookingChangeRequest, admin: AdminUser, decision_note: str = "") -> BookingChangeRequest:
    agreement = bcr.agreement
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    bcr = _expire_if_overdue(db, bcr)
    if bcr.status != "PENDING":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only a PENDING request can be declined (current status: {bcr.status})")

    bcr.status = "DECLINED"
    bcr.decided_by_admin_id = admin.id
    bcr.decided_at = datetime.now(timezone.utc)
    bcr.decision_note = decision_note
    db.commit()
    db.refresh(bcr)

    change_label = _change_label(bcr.change_type)
    notif_crud.notify_user_by_guest(
        db, bcr.requested_by,
        title=f"Your {change_label} was declined",
        message=f"Your requested {change_label} was declined." + (f" Reason: {decision_note}" if decision_note else ""),
        notification_type="booking_change_request.declined",
        related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
    )
    return bcr


def withdraw_change_request(db: Session, user: UserAccount, bcr: BookingChangeRequest) -> BookingChangeRequest:
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != bcr.requested_by_guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This request does not belong to you")
    bcr = _expire_if_overdue(db, bcr)
    if bcr.status != "PENDING":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only a PENDING request can be withdrawn (current status: {bcr.status})")

    bcr.status = "WITHDRAWN"
    db.commit()
    db.refresh(bcr)
    return bcr


def list_change_requests_for_guest(db: Session, user: UserAccount) -> list[BookingChangeRequest]:
    guest = get_guest_for_user(db, user)
    if not guest:
        return []
    query = (
        select(BookingChangeRequest)
        .where(BookingChangeRequest.requested_by_guest_id == guest.id)
        .order_by(BookingChangeRequest.created_at.desc())
    )
    return list(db.scalars(query))


def list_change_requests_for_admin(db: Session, admin: AdminUser) -> list[BookingChangeRequest]:
    from app.models.listing import Listing

    query = (
        select(BookingChangeRequest)
        .join(Agreement, Agreement.id == BookingChangeRequest.agreement_id)
        .join(Offer, Offer.id == Agreement.offer_id)
    )
    if admin.role != "super_admin":
        query = query.join(Listing, Listing.id == Offer.listing_id).where(Listing.owner_id == admin.id)
    query = query.order_by(BookingChangeRequest.created_at.desc())
    return list(db.scalars(query))
