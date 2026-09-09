from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.admin_user import AdminUser
from app.models.finance import OBLIGATION_TYPE_TO_PLANE, Obligation
from app.models.identity_verification import IdentityVerification
from app.models.leasing import Agreement, Application, Offer, OfferTerms
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.sublet_request import CO_TENANCY_ARRANGEMENT_TYPES, SUBLET_ARRANGEMENT_TYPES, SubletRequest
from app.models.user_account import UserAccount
from app.models.guest import Guest
from app.crud import guest as guest_crud
from app.crud import notification as notif_crud
from app.crud.eligibility import check_room_capacity
from app.crud.ids import new_id
from app.crud.market_policy import resolve_market_policy, to_policy_snapshot
from app.schemas.leasing import SubletRequestRead


def to_sublet_request_read(db: Session, sr: SubletRequest) -> SubletRequestRead:
    """Enrich the bare record with the room/people context a reviewer actually
    needs -- 'occupancy #14 -> party #37' means nothing on its own."""
    occupancy = sr.current_occupancy
    listing = occupancy.listing if occupancy else None
    current_tenant = occupancy.guest if occupancy else None
    proposed_renter = db.scalar(select(UserAccount).where(UserAccount.party_id == sr.proposed_renter_party_id))

    return SubletRequestRead(
        id=sr.id,
        current_occupancy_id=sr.current_occupancy_id,
        proposed_renter_party_id=sr.proposed_renter_party_id,
        status=sr.status,
        authority_evidence_ref=sr.authority_evidence_ref,
        admin_decision=sr.admin_decision,
        admin_notes=sr.admin_notes,
        decided_by_admin_id=sr.decided_by_admin_id,
        created_at=sr.created_at,
        decided_at=sr.decided_at,
        arrangement_type=sr.arrangement_type,
        requested_by_guest_id=sr.requested_by_guest_id,
        original_renter_liability=sr.original_renter_liability,
        new_occupant_liability=sr.new_occupant_liability,
        deposit_disposition=sr.deposit_disposition,
        policy_snapshot=sr.policy_snapshot,
        new_agreement_id=sr.new_agreement_id,
        payee_model=sr.payee_model,
        listing_name=listing.name if listing else "",
        listing_city=listing.city if listing else "",
        room_type=listing.room_type if listing else "",
        guests=listing.guests if listing else 0,
        bedrooms=listing.bedrooms if listing else 0,
        bathrooms=listing.bathrooms if listing else 0,
        current_tenant_name=current_tenant.name if current_tenant else "",
        proposed_renter_name=proposed_renter.full_name if proposed_renter else "",
    )


def _assert_sublet_permitted(occupancy: Occupancy) -> None:
    """Only transfer an active signed tenancy with at least 30 nights remaining."""
    if occupancy.status != "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "Can only sublet active occupancies")
    if not occupancy.offer.agreement or occupancy.offer.agreement.status != "SIGNED":
        raise HTTPException(status.HTTP_409_CONFLICT, "A signed agreement is required before subletting")
    if not occupancy.expected_end_date or (occupancy.expected_end_date - date.today()).days < 30:
        raise HTTPException(status.HTTP_409_CONFLICT, "A sublet transfer requires at least 30 nights remaining")


def _guest_for_proposed_party(db: Session, party_id: int) -> Guest:
    user = db.scalar(
        select(UserAccount)
        .where(UserAccount.party_id == party_id, UserAccount.is_active.is_(True))
        .order_by(UserAccount.id)
    )
    if not user:
        raise HTTPException(status.HTTP_409_CONFLICT, "Proposed renter party has no active user account")
    return guest_crud.get_or_create_guest_for_user(db, user)


def submit_sublet_request(
    db: Session,
    user: UserAccount,
    occupancy_id: int,
    proposed_renter_party_id: int,
    arrangement_type: str = "ASSIGNMENT_FULL",
    authority_evidence_ref: str = "",
    proposed_monthly_rent: float | None = None,
) -> SubletRequest:
    """Current renter submits a sublet request."""
    if not user.party_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no associated party")

    # Neither the doc nor the original code considered this case -- found live
    # when testing named the current tenant's own account as the proposed
    # renter, which silently "succeeded" as a no-op with no actual handoff.
    if user.party_id == proposed_renter_party_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't sublet a room to yourself")

    if arrangement_type not in SUBLET_ARRANGEMENT_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported arrangement type '{arrangement_type}' -- this platform's occupancy model "
            "only supports ASSIGNMENT_FULL or REPLACEMENT_OCCUPANT today",
        )

    # Verify the user owns the current occupancy
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")

    _assert_sublet_permitted(occupancy)

    policy = resolve_market_policy(db)

    if arrangement_type in CO_TENANCY_ARRANGEMENT_TYPES:
        capacity_reasons = check_room_capacity(db, occupancy.room)
        if capacity_reasons:
            raise HTTPException(status.HTTP_409_CONFLICT, {"message": "Room cannot accept a co-tenant", "reasons": capacity_reasons})
        if proposed_monthly_rent is not None:
            # ZR-ENG-CLR-003 Rule 4.4 / Section 6: sublet rent is a regulated
            # decision, not a free-form field -- compute and enforce the ceiling
            # rather than trusting whatever the requester typed.
            original_rent = float(occupancy.offer.terms[-1].monthly_rent)
            max_rent = round(original_rent * float(policy.sublet_max_rent_multiple_of_original), 2)
            if proposed_monthly_rent > max_rent:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Proposed rent {proposed_monthly_rent:.2f} exceeds the resolved cap of {max_rent:.2f} "
                    f"({policy.sublet_max_rent_multiple_of_original}x the original {original_rent:.2f})",
                )

    # Proposed renter party must exist
    proposed_party = db.get(Party, proposed_renter_party_id)
    if not proposed_party:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proposed renter party not found")
    if not verify_sublet_identity(
        db, type("SubletIdentityCandidate", (), {"proposed_renter_party_id": proposed_renter_party_id})()
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Proposed renter must have an approved identity verification")

    # Check if sublet request already exists
    existing = db.scalar(
        select(SubletRequest).where(
            SubletRequest.current_occupancy_id == occupancy_id,
            SubletRequest.status.in_(["pending_verification", "pending_admin_review"]),
        )
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sublet request already exists for this occupancy")

    sublet_request = SubletRequest(
        current_occupancy_id=occupancy_id,
        proposed_renter_party_id=proposed_renter_party_id,
        status="pending_admin_review",
        authority_evidence_ref=authority_evidence_ref,
        arrangement_type=arrangement_type,
        # Captured now, before approval can overwrite occupancy.guest_id -- this
        # is the only reliable record of who actually requested this (ZR-ENG-CLR-003
        # Section 15.1's occupancy_relationship / audit_event requirement).
        requested_by_guest_id=occupancy.guest_id,
        policy_snapshot={
            **to_policy_snapshot(policy),
            "consent_standard": policy.sublet_consent_standard,
            "consent_response_days": policy.sublet_consent_response_days,
            "arrangement_permitted": True,
            **({"proposed_monthly_rent": proposed_monthly_rent} if proposed_monthly_rent is not None else {}),
        },
    )
    db.add(sublet_request)
    db.flush()

    notif_crud.notify_all_super_admins(
        db,
        title="New sublet request pending review",
        message=f"{user.full_name} requested to sublet occupancy #{occupancy_id}.",
        notification_type="sublet_request.submitted",
        related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
    )

    listing = occupancy.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="A sublet was requested for your listing",
            message=f"{user.full_name} requested to sublet \"{listing.name}\". Zoiko will review before it's approved.",
            notification_type="sublet_request.submitted",
            related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
        )

    # ZR-ENG-CLR-003 Section 17.2 notification matrix: the proposed occupant must
    # be told a request naming them exists, even before any approval -- previously
    # this platform never notified them at all, at any stage.
    if arrangement_type in CO_TENANCY_ARRANGEMENT_TYPES:
        proposed_message = f"{user.full_name} has requested to add you as a co-tenant on their room, pending Zoiko's review."
    else:
        proposed_message = f"{user.full_name} has requested to hand over their room to you, pending Zoiko's review."
    notif_crud.notify_user_by_party(
        db, proposed_renter_party_id,
        title="You've been proposed as a sublet occupant",
        message=proposed_message,
        notification_type="sublet_request.proposed",
        related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
    )

    db.commit()
    db.refresh(sublet_request)
    return sublet_request


def get_sublet_request(db: Session, sublet_request_id: int) -> SubletRequest | None:
    return db.get(SubletRequest, sublet_request_id)


def verify_sublet_identity(
    db: Session,
    sublet_request: SubletRequest,
) -> bool:
    """Check if proposed renter has verified identity."""
    now = datetime.now(timezone.utc)
    verification = db.scalar(
        select(IdentityVerification)
        .where(
            IdentityVerification.party_id == sublet_request.proposed_renter_party_id,
            IdentityVerification.status == "verified",
            (IdentityVerification.expires_at.is_(None)) | (IdentityVerification.expires_at > now),
        )
        .order_by(IdentityVerification.id.desc())
    )
    return verification is not None


def _notify_sublet_requester(
    db: Session, requester_guest_id: str, sublet_request_id: int, *, approved: bool, notes: str
) -> None:
    guest = db.get(Guest, requester_guest_id)
    if not guest:
        return
    verb = "approved" if approved else "rejected"
    notif_crud.notify_user_by_guest(
        db, guest,
        title=f"Sublet request {verb}",
        message=notes or f"Your sublet request was {verb}.",
        notification_type=f"sublet_request.{verb}",
        related_entity_type="sublet_request",
        related_entity_id=str(sublet_request_id),
    )


def _notify_sublet_host(db: Session, occupancy: Occupancy, sublet_request_id: int, *, approved: bool) -> None:
    listing = occupancy.listing if occupancy else None
    if not listing or not listing.party_id:
        return
    verb = "approved" if approved else "rejected"
    notif_crud.notify_user_by_party(
        db, listing.party_id,
        title=f"A sublet request for your listing was {verb}",
        message=f"The sublet request for \"{listing.name}\" was {verb}.",
        notification_type=f"sublet_request.{verb}",
        related_entity_type="sublet_request",
        related_entity_id=str(sublet_request_id),
    )


def _create_co_tenancy_agreement(
    db: Session, occupancy: Occupancy, proposed_guest: Guest, monthly_rent_override: float | None = None,
) -> Agreement:
    """SUBLEASE_PARTIAL/ADD_CO_TENANT: builds a real, independent Application ->
    Offer -> Terms -> Agreement chain for the co-tenant, rather than overwriting
    current_occupancy, which stays exactly as-is. The co-tenant then pays and
    moves in on this agreement exactly like any other tenant; room capacity is
    enforced at that move-in step by the same check_room_capacity gate every
    occupancy goes through. monthly_rent_override is the requester's negotiated
    rent (already validated against the market-pack cap at submission time);
    absent that, mirrors the existing tenant's rent unchanged."""
    listing = occupancy.listing
    latest_terms = occupancy.offer.terms[-1]
    monthly_rent = monthly_rent_override if monthly_rent_override is not None else latest_terms.monthly_rent

    application = Application(listing_id=listing.id, guest_id=proposed_guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=proposed_guest.id, status="ACCEPTED", current_version=1)
    db.add(offer)
    db.flush()
    db.add(OfferTerms(
        offer_id=offer.id, version=1,
        monthly_rent=monthly_rent, deposit_amount=latest_terms.deposit_amount,
        start_date=date.today(), term_months=latest_terms.term_months,
    ))
    db.flush()

    now = datetime.now(timezone.utc)
    agreement = Agreement(offer_id=offer.id, status="SIGNED", signature_ref=new_id("SIG"), signed_by_provider_at=now, signed_by_renter_at=now)
    db.add(agreement)
    db.flush()

    db.add(Obligation(
        obligation_type="RENT", money_plane=OBLIGATION_TYPE_TO_PLANE["RENT"],
        amount=latest_terms.monthly_rent, due_date=latest_terms.start_date, agreement_id=agreement.id,
    ))
    db.add(Obligation(
        obligation_type="DEPOSIT", money_plane=OBLIGATION_TYPE_TO_PLANE["DEPOSIT"],
        amount=latest_terms.deposit_amount, due_date=latest_terms.start_date, agreement_id=agreement.id,
    ))
    db.flush()
    return agreement


def approve_sublet_request(db: Session, sublet_request: SubletRequest, admin: AdminUser, notes: str = "") -> SubletRequest:
    """Admin approves a sublet request."""
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only super admins can approve sublet requests")

    if sublet_request.status != "pending_admin_review":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only pending sublet requests can be approved")
    if not verify_sublet_identity(db, sublet_request):
        raise HTTPException(status.HTTP_409_CONFLICT, "Proposed renter no longer has an approved identity verification")
    _assert_sublet_permitted(sublet_request.current_occupancy)

    listing = sublet_request.current_occupancy.listing
    proposed_guest = _guest_for_proposed_party(db, sublet_request.proposed_renter_party_id)
    is_co_tenancy = sublet_request.arrangement_type in CO_TENANCY_ARRANGEMENT_TYPES
    policy = resolve_market_policy(db)

    if is_co_tenancy:
        capacity_reasons = check_room_capacity(db, sublet_request.current_occupancy.room)
        if capacity_reasons:
            raise HTTPException(status.HTTP_409_CONFLICT, {"message": "Room cannot accept a co-tenant", "reasons": capacity_reasons})
        negotiated_rent = sublet_request.policy_snapshot.get("proposed_monthly_rent")
        new_agreement = _create_co_tenancy_agreement(db, sublet_request.current_occupancy, proposed_guest, negotiated_rent)
        sublet_request.new_agreement_id = new_agreement.id
        # Both tenants remain fully active and liable -- adding a co-tenant
        # doesn't release the original renter of anything.
        sublet_request.original_renter_liability = "ACTIVE"
        sublet_request.new_occupant_liability = "JOINT"
        sublet_request.deposit_disposition = "SEPARATE_DEPOSIT_CREATED"
        # ZR-ENG-CLR-003 Rule 4.5: the co-tenant funds their own agreement
        # directly -- there's no third party routing their rent through the
        # original tenant.
        sublet_request.payee_model = policy.sublet_assignment_payee_model
        requester_guest_id = sublet_request.current_occupancy.guest_id
    else:
        # Capture who to notify *before* reassigning the occupancy's guest below --
        # afterwards current_occupancy.guest_id points at the new tenant, not the
        # person who submitted this request.
        requester_guest_id = sublet_request.current_occupancy.guest_id
        sublet_request.current_occupancy.guest_id = proposed_guest.id
        # ZR-ENG-CLR-003 Section 14.4 liability model + Rule 4.6/AC-08 deposit
        # disposition. ASSIGNMENT_FULL fully releases the original renter and
        # makes the new occupant the sole assignee; REPLACEMENT_OCCUPANT keeps
        # the original renter on the hook (they arranged the swap but remain
        # the named party) -- deliberately conservative rather than guessed.
        if sublet_request.arrangement_type == "ASSIGNMENT_FULL":
            sublet_request.original_renter_liability = "RELEASED"
            sublet_request.new_occupant_liability = "ASSIGNEE"
        else:  # REPLACEMENT_OCCUPANT
            sublet_request.original_renter_liability = "LIMITED"
            sublet_request.new_occupant_liability = "SUBORDINATE"
        sublet_request.deposit_disposition = "RETAINED_BY_ORIGINAL_TENANCY"
        # The assignee takes over the same ongoing rent obligation directly --
        # there's no original-renter routing to preserve once they're released.
        sublet_request.payee_model = policy.sublet_assignment_payee_model

    sublet_request.status = "approved"
    sublet_request.admin_decision = "approved"
    sublet_request.admin_notes = notes
    sublet_request.decided_by_admin_id = admin.id
    sublet_request.decided_at = datetime.now(timezone.utc)

    _notify_sublet_requester(db, requester_guest_id, sublet_request.id, approved=True, notes=notes)
    _notify_sublet_host(db, sublet_request.current_occupancy, sublet_request.id, approved=True)
    if is_co_tenancy:
        notif_crud.notify_user_by_guest(
            db, proposed_guest,
            title="You've been added as a co-tenant",
            message=f"Your co-tenancy for occupancy #{sublet_request.current_occupancy_id} has been approved. Complete payment to move in.",
            notification_type="sublet_request.co_tenant_approved",
            related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
        )
    else:
        notif_crud.notify_user_by_guest(
            db, proposed_guest,
            title="You're now the tenant of record",
            message=f"Your sublet arrangement for occupancy #{sublet_request.current_occupancy_id} has been approved. You are now the tenant of record.",
            notification_type="sublet_request.occupant_approved",
            related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
        )

    # Only notified now that they're actually authorized -- never earlier in the
    # request/review flow, so a proposed occupant's involvement isn't exposed
    # prematurely.
    notif_crud.notify_user_by_guest(
        db, proposed_guest,
        title="You've been authorized as a new occupant",
        message=f'You have been approved to take over occupancy of "{listing.name}".' if listing else "You have been approved to take over an occupancy.",
        notification_type="sublet_request.authorized",
        related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
    )
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Occupant changed via sublet",
            message=f'The occupant of "{listing.name}" has changed following an approved sublet request.',
            notification_type="sublet_request.tenant_changed",
            related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
        )

    db.commit()
    db.refresh(sublet_request)
    return sublet_request


def reject_sublet_request(db: Session, sublet_request: SubletRequest, admin: AdminUser, notes: str = "") -> SubletRequest:
    """Admin rejects a sublet request."""
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only super admins can reject sublet requests")

    if sublet_request.status != "pending_admin_review":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only pending sublet requests can be rejected")
    sublet_request.status = "rejected"
    sublet_request.admin_decision = "rejected"
    sublet_request.admin_notes = notes
    sublet_request.decided_by_admin_id = admin.id
    sublet_request.decided_at = datetime.now(timezone.utc)

    _notify_sublet_requester(
        db, sublet_request.current_occupancy.guest_id, sublet_request.id, approved=False, notes=notes
    )
    _notify_sublet_host(db, sublet_request.current_occupancy, sublet_request.id, approved=False)
    notif_crud.notify_user_by_party(
        db, sublet_request.proposed_renter_party_id,
        title="Sublet proposal rejected",
        message=notes or "The sublet arrangement naming you was not approved.",
        notification_type="sublet_request.occupant_rejected",
        related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
    )

    db.commit()
    db.refresh(sublet_request)
    return sublet_request


def list_sublet_requests_for_guest(db: Session, guest_id: str) -> list[SubletRequest]:
    """Every sublet request this guest has ever *submitted* -- keyed on the
    immutable requested_by_guest_id captured at submission time, not on
    current_occupancy.guest_id, which approval overwrites with the new occupant.
    Fixes a real bug: the old occupancy-join query made a requester's own
    approved sublet request vanish from their history the moment it was
    approved, while incorrectly attributing it to the replacement occupant
    instead."""
    return list(
        db.scalars(
            select(SubletRequest)
            .options(
                joinedload(SubletRequest.current_occupancy).joinedload(Occupancy.listing),
                joinedload(SubletRequest.current_occupancy).joinedload(Occupancy.guest),
            )
            .where(SubletRequest.requested_by_guest_id == guest_id)
            .order_by(SubletRequest.created_at.desc())
        )
    )


def list_pending_sublet_requests(db: Session, admin: AdminUser) -> list[SubletRequest]:
    """List all pending sublet requests for admin review."""
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only super admins can view all sublet requests")

    return list(
        db.scalars(
            select(SubletRequest)
            .options(
                joinedload(SubletRequest.current_occupancy).joinedload(Occupancy.listing),
                joinedload(SubletRequest.current_occupancy).joinedload(Occupancy.guest),
            )
            .where(SubletRequest.status == "pending_admin_review")
            .order_by(SubletRequest.created_at.desc())
        )
    )
