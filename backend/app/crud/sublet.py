from datetime import date, datetime, timezone
from io import BytesIO

from fastapi import HTTPException, status
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.admin_user import AdminUser
from app.models.finance import OBLIGATION_TYPE_TO_PLANE, Obligation
from app.models.identity_verification import IdentityVerification
from app.models.leasing import Agreement, Application, Offer, OfferTerms
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.sublet_request import (
    CO_TENANCY_ARRANGEMENT_TYPES,
    NO_TENANCY_ARRANGEMENT_TYPES,
    REPLACING_ARRANGEMENT_TYPES,
    SUBLET_ARRANGEMENT_TYPES,
    SubletRequest,
)
from app.models.user_account import UserAccount
from app.models.guest import Guest
from app.crud import guest as guest_crud
from app.crud import notification as notif_crud
from app.crud.eligibility import check_room_capacity
from app.crud.ids import new_id
from app.crud.market_policy import jurisdiction_code_for_occupancy, resolve_market_policy, to_policy_snapshot
from app.crud.party import party_id_for_listing
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
        decided_by_user_id=sr.decided_by_user_id,
        created_at=sr.created_at,
        decided_at=sr.decided_at,
        info_request_note=sr.info_request_note,
        info_requested_at=sr.info_requested_at,
        info_response_note=sr.info_response_note,
        info_responded_at=sr.info_responded_at,
        approval_conditions=sr.approval_conditions,
        approval_expires_at=sr.approval_expires_at,
        withdrawn_at=sr.withdrawn_at,
        reason=sr.reason,
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


def _assert_sublet_permitted(db: Session, occupancy: Occupancy) -> None:
    """Only transfer an active signed tenancy with at least 30 nights remaining."""
    if occupancy.status != "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "Can only sublet active occupancies")
    if not occupancy.offer.agreement or occupancy.offer.agreement.status != "SIGNED":
        raise HTTPException(status.HTTP_409_CONFLICT, "A signed agreement is required before subletting")
    if not occupancy.expected_end_date or (occupancy.expected_end_date - date.today()).days < 30:
        raise HTTPException(status.HTTP_409_CONFLICT, "A sublet transfer requires at least 30 nights remaining")

    # ZR-SUB-003 Section 15 edge case: "No verified landlord/agent | Block
    # submission; provide resolution path; do not route to an unverified
    # contact." Now that the Host decides (see _assert_can_decide_sublet), a
    # listing with no linked, active Host login is a dead end -- the request
    # would sit forever with nobody able to see or act on it.
    has_verified_host = db.scalar(
        select(UserAccount.id).where(
            UserAccount.party_id == party_id_for_listing(occupancy.listing),
            UserAccount.is_active.is_(True),
        ).limit(1)
    )
    if not has_verified_host:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This listing has no verified landlord/agent account to route this request to yet -- "
            "contact Zoiko support before submitting a sublet request",
        )

    # Product rule: an occupancy may change hands via ASSIGNMENT_FULL/REPLACEMENT_OCCUPANT
    # once. The occupant that receives it that way cannot sublet it onward again --
    # Host -> Anil -> Priya -> (end). Anil's own occupancy row is what Priya now
    # occupies (approval overwrites Occupancy.guest_id in place, see
    # approve_sublet_request below), so a prior approved replacing-type request on
    # this exact occupancy_id is proof it already changed hands once.
    already_reassigned = db.scalar(
        select(SubletRequest.id).where(
            SubletRequest.current_occupancy_id == occupancy.id,
            SubletRequest.status == "approved",
            SubletRequest.arrangement_type.in_(REPLACING_ARRANGEMENT_TYPES),
        ).limit(1)
    )
    if already_reassigned:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This room has already been assigned to a new occupant once -- it cannot be sublet again",
        )


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
    reason: str = "",
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
            f"Unsupported arrangement type '{arrangement_type}' -- this platform supports "
            f"{', '.join(SUBLET_ARRANGEMENT_TYPES)} today",
        )

    # Verify the user owns the current occupancy
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")

    _assert_sublet_permitted(db, occupancy)

    policy = resolve_market_policy(db, jurisdiction_code_for_occupancy(occupancy))

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
        reason=reason.strip(),
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

    # ZR-SUB-003 IMPLEMENTATION LOCK: routed to the verified Host, not blasted to
    # every Zoiko super admin -- "Zoiko Rooms records and routes the request; it
    # does not grant permission on the owner's behalf." Admins retain their own
    # legal-ops override visibility via the admin sublet-requests queue without
    # needing a push notification for every single submission.
    listing = occupancy.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="A sublet was requested for your listing",
            message=f'{user.full_name} requested to sublet "{listing.name}". Review it in your Sublet Requests dashboard.',
            notification_type="sublet_request.submitted",
            related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
        )

    # ZR-ENG-CLR-003 Section 17.2 notification matrix: the proposed occupant must
    # be told a request naming them exists, even before any approval -- previously
    # this platform never notified them at all, at any stage.
    if arrangement_type in CO_TENANCY_ARRANGEMENT_TYPES:
        proposed_message = f"{user.full_name} has requested to add you as a co-tenant on their room, pending the host's review."
    elif arrangement_type in NO_TENANCY_ARRANGEMENT_TYPES:
        proposed_message = f"{user.full_name} has requested permission for you to reside in their room, pending the host's review."
    else:
        proposed_message = f"{user.full_name} has requested to hand over their room to you, pending the host's review."
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


def list_sublet_requests_for_host(db: Session, user: "UserAccount") -> list[SubletRequest]:
    """ZR-SUB-003 Section 2: 'Provider navigation: Dashboard -> Rentals ->
    Requests -> Sublet' -- every sublet request routed to any of this Host's
    own party-owned listings, newest first."""
    if not user.party_id:
        return []
    return list(
        db.scalars(
            select(SubletRequest)
            .join(Occupancy, Occupancy.id == SubletRequest.current_occupancy_id)
            .join(Listing, Listing.id == Occupancy.listing_id)
            .where(Listing.party_id == user.party_id)
            .options(joinedload(SubletRequest.current_occupancy))
            .order_by(SubletRequest.created_at.desc())
        )
    )


def get_sublet_request_for_host_or_404(db: Session, sublet_request_id: int, user: "UserAccount") -> SubletRequest:
    sublet_request = db.get(SubletRequest, sublet_request_id)
    if not sublet_request:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sublet request not found")
    listing = sublet_request.current_occupancy.listing
    if not user.party_id or party_id_for_listing(listing) != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view sublet requests for your own listings")
    return sublet_request


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


def _add_months(d: date, months: int) -> date:
    """Same calendar-month arithmetic as crud/occupancy.py's own _add_months --
    duplicated rather than imported to avoid a cross-module dependency on a
    private helper."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def _whole_months_between(start: date, end: date) -> int:
    """The largest N such that _add_months(start, N) <= end."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if _add_months(start, months) > end:
        months -= 1
    return max(months, 0)


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
    start_date = date.today()

    # ZR-ENG-CLR-003 Rule 4.9 DATE INVARIANT: "sublet_end <= master_occupancy_end
    # ... may never silently extend the superior booking." Copying the master
    # tenancy's own term_months verbatim (the previous behavior) let a co-tenant's
    # subordinate agreement run past the master occupancy's real end date, since
    # that original term started earlier than today.
    term_months = latest_terms.term_months
    if occupancy.expected_end_date:
        max_months = _whole_months_between(start_date, occupancy.expected_end_date)
        if max_months < 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "Not enough time remains on the master occupancy for a co-tenancy term")
        term_months = min(term_months, max_months)

    application = Application(listing_id=listing.id, guest_id=proposed_guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=proposed_guest.id, status="ACCEPTED", current_version=1)
    db.add(offer)
    db.flush()
    db.add(OfferTerms(
        offer_id=offer.id, version=1,
        monthly_rent=monthly_rent, deposit_amount=latest_terms.deposit_amount,
        start_date=start_date, term_months=term_months,
    ))
    db.flush()

    now = datetime.now(timezone.utc)
    agreement = Agreement(offer_id=offer.id, status="SIGNED", signature_ref=new_id("SIG"), signed_by_provider_at=now, signed_by_renter_at=now)
    db.add(agreement)
    db.flush()

    db.add(Obligation(
        obligation_type="RENT", money_plane=OBLIGATION_TYPE_TO_PLANE["RENT"],
        amount=latest_terms.monthly_rent, currency=listing.currency, due_date=latest_terms.start_date, agreement_id=agreement.id,
    ))
    db.add(Obligation(
        obligation_type="DEPOSIT", money_plane=OBLIGATION_TYPE_TO_PLANE["DEPOSIT"],
        amount=latest_terms.deposit_amount, currency=listing.currency, due_date=latest_terms.start_date, agreement_id=agreement.id,
    ))
    db.flush()
    return agreement


def _assert_can_decide_sublet(sublet_request: SubletRequest, actor: "AdminUser | UserAccount") -> None:
    """ZR-SUB-003 IMPLEMENTATION LOCK: 'A tenant's request for permission to
    sublet must be sent to the verified landlord, agent or other authorized
    property representative. Zoiko Rooms records and routes the request; it
    does not grant permission on the owner's behalf.' The Host (the party that
    owns the listing) is the real decision-maker; a super_admin deciding is
    kept only as a legal-ops override, per the doc's permissions matrix
    ('Admin/Support: Never on behalf of owner except approved legal ops
    workflow'), not the ordinary path."""
    if isinstance(actor, AdminUser):
        if actor.role != "super_admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only super admins can decide sublet requests on the platform's behalf")
        return
    listing = sublet_request.current_occupancy.listing
    if not actor.party_id or party_id_for_listing(listing) != actor.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only decide sublet requests for your own listings")


def approve_sublet_request(
    db: Session, sublet_request: SubletRequest, actor: "AdminUser | UserAccount", notes: str = "",
    conditions: str = "", expires_at: datetime | None = None,
) -> SubletRequest:
    """The verified Host approves a sublet request (or, exceptionally, a super
    admin acting as a legal-ops override -- see _assert_can_decide_sublet).
    ZR-SUB-003 Section 5.2: conditions/expires_at are optional, descriptive
    terms attached to the approval (see the model's own note on why expiry
    isn't automatically enforced)."""
    _assert_can_decide_sublet(sublet_request, actor)
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Approval expiry must be in the future")

    if sublet_request.status != "pending_admin_review":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only pending sublet requests can be approved")
    if not verify_sublet_identity(db, sublet_request):
        raise HTTPException(status.HTTP_409_CONFLICT, "Proposed renter no longer has an approved identity verification")
    _assert_sublet_permitted(db, sublet_request.current_occupancy)

    listing = sublet_request.current_occupancy.listing
    proposed_guest = _guest_for_proposed_party(db, sublet_request.proposed_renter_party_id)
    is_co_tenancy = sublet_request.arrangement_type in CO_TENANCY_ARRANGEMENT_TYPES
    is_no_tenancy = sublet_request.arrangement_type in NO_TENANCY_ARRANGEMENT_TYPES
    policy = resolve_market_policy(db, jurisdiction_code_for_occupancy(sublet_request.current_occupancy))

    if is_no_tenancy:
        # ADDITIONAL_OCCUPANT: "permitted to reside without becoming a
        # contractual tenant" (Section 3) -- no agreement, no obligation, no
        # occupancy row, no capacity check (they're not counted as a tenancy).
        # This is a record of permission only.
        sublet_request.original_renter_liability = "ACTIVE"
        sublet_request.new_occupant_liability = "NONE"
        sublet_request.deposit_disposition = "NOT_APPLICABLE"
        sublet_request.payee_model = "EXTERNAL_PAYEE_RECORDED"
        requester_guest_id = sublet_request.current_occupancy.guest_id
    elif is_co_tenancy:
        capacity_reasons = check_room_capacity(db, sublet_request.current_occupancy.room)
        if capacity_reasons:
            raise HTTPException(status.HTTP_409_CONFLICT, {"message": "Room cannot accept a co-tenant", "reasons": capacity_reasons})
        negotiated_rent = sublet_request.policy_snapshot.get("proposed_monthly_rent")
        new_agreement = _create_co_tenancy_agreement(db, sublet_request.current_occupancy, proposed_guest, negotiated_rent)
        sublet_request.new_agreement_id = new_agreement.id
        # Both tenants remain fully active and liable -- adding a co-tenant
        # doesn't release the original renter of anything. A licensee/lodger
        # has no tenancy rights (Section 3's canonical meaning), unlike a true
        # co-tenant/sublease occupant who shares equal standing -- hence the
        # distinct liability outcome despite reusing the same agreement path.
        sublet_request.original_renter_liability = "ACTIVE"
        sublet_request.new_occupant_liability = (
            "LICENSEE" if sublet_request.arrangement_type == "LODGER_OR_LICENSEE" else "JOINT"
        )
        sublet_request.deposit_disposition = "SEPARATE_DEPOSIT_CREATED"
        # ZR-ENG-CLR-003 Rule 4.5: the co-tenant funds their own agreement
        # directly -- there's no third party routing their rent through the
        # original tenant. This is the sublease/co-tenancy payee model
        # (market_policy.py's sublet_sublease_payee_model), distinct from
        # sublet_assignment_payee_model below -- an assignment hands the
        # *entire existing* tenancy's payment relationship to one new
        # occupant, while a co-tenancy/sublease creates an independent one
        # alongside the original.
        sublet_request.payee_model = policy.sublet_sublease_payee_model
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
            # The assignee becomes the sole named party on the agreement itself,
            # not just the occupancy -- otherwise every offer/agreement-scoped
            # action (booking change requests, disclosures, agreement PDF access)
            # keeps authorizing the released original renter instead of the
            # assignee who actually now holds the tenancy.
            sublet_request.current_occupancy.offer.guest_id = proposed_guest.id
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
    sublet_request.approval_conditions = conditions.strip()
    sublet_request.approval_expires_at = expires_at
    if isinstance(actor, AdminUser):
        sublet_request.decided_by_admin_id = actor.id
    else:
        sublet_request.decided_by_user_id = actor.id
    sublet_request.decided_at = datetime.now(timezone.utc)

    _notify_sublet_requester(db, requester_guest_id, sublet_request.id, approved=True, notes=notes)
    _notify_sublet_host(db, sublet_request.current_occupancy, sublet_request.id, approved=True)
    if is_no_tenancy:
        notif_crud.notify_user_by_guest(
            db, proposed_guest,
            title="You've been given permission to reside",
            message=f'You have been approved to reside at "{listing.name if listing else "this property"}" '
                    "-- this is a residency permission, not a tenancy: no rent obligation, no occupancy record.",
            notification_type="sublet_request.additional_occupant_approved",
            related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
        )
    elif is_co_tenancy:
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
    # prematurely. Skipped for ADDITIONAL_OCCUPANT: "take over occupancy" would
    # be factually wrong for a permission-only record with no tenancy at all --
    # that case already got its own accurate message above.
    if not is_no_tenancy:
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


def reject_sublet_request(db: Session, sublet_request: SubletRequest, actor: "AdminUser | UserAccount", notes: str = "") -> SubletRequest:
    """The verified Host declines a sublet request (or, exceptionally, a super
    admin acting as a legal-ops override -- see _assert_can_decide_sublet)."""
    _assert_can_decide_sublet(sublet_request, actor)

    if sublet_request.status != "pending_admin_review":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only pending sublet requests can be rejected")
    sublet_request.status = "rejected"
    sublet_request.admin_decision = "rejected"
    sublet_request.admin_notes = notes
    if isinstance(actor, AdminUser):
        sublet_request.decided_by_admin_id = actor.id
    else:
        sublet_request.decided_by_user_id = actor.id
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


def request_more_sublet_info(
    db: Session, sublet_request: SubletRequest, actor: "AdminUser | UserAccount", note: str,
) -> SubletRequest:
    """ZR-SUB-003 Section 5.1: 'MORE_INFORMATION_REQUESTED | Recipient needs
    additional information | Landlord/Agent.' The same decision authority as
    approve/reject (Host, or an admin legal-ops override)."""
    _assert_can_decide_sublet(sublet_request, actor)
    if sublet_request.status != "pending_admin_review":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a pending sublet request can have more information requested")
    if not note.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Describe what information you need from the tenant")

    sublet_request.status = "more_information_requested"
    sublet_request.info_request_note = note.strip()
    sublet_request.info_requested_at = datetime.now(timezone.utc)

    notif_crud.notify_user_by_guest(
        db, sublet_request.current_occupancy.guest_id,
        title="More information needed for your sublet request",
        message=note.strip(),
        notification_type="sublet_request.more_information_requested",
        related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
    )
    db.commit()
    db.refresh(sublet_request)
    return sublet_request


def respond_to_sublet_info_request(db: Session, sublet_request: SubletRequest, user: "UserAccount", response_note: str) -> SubletRequest:
    """ZR-SUB-003 Section 5.1: 'The tenant may respond with an additive
    submission; the original request remains intact.' Only the original
    requester (requested_by_guest_id, captured at submission -- immune to
    approval later overwriting current_occupancy.guest_id) may respond."""
    guest = guest_crud.get_guest_for_user(db, user)
    if not guest or sublet_request.requested_by_guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only respond to your own sublet request")
    if sublet_request.status != "more_information_requested":
        raise HTTPException(status.HTTP_409_CONFLICT, "This sublet request is not awaiting more information")
    if not response_note.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provide the requested information before responding")

    sublet_request.info_response_note = response_note.strip()
    sublet_request.info_responded_at = datetime.now(timezone.utc)
    # Back to the decision-maker's queue -- doc's TENANT_RESPONSE_SUBMITTED is
    # transient here, not a separate resting state, since nothing further needs
    # to happen to it before it's awaiting decision again.
    sublet_request.status = "pending_admin_review"

    listing = sublet_request.current_occupancy.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Tenant responded to your sublet information request",
            message=response_note.strip(),
            notification_type="sublet_request.tenant_response_submitted",
            related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
        )
    db.commit()
    db.refresh(sublet_request)
    return sublet_request


# States a tenant can still withdraw from -- a final decision (approved/
# rejected) or an already-withdrawn request can never be withdrawn again.
_WITHDRAWABLE_STATUSES = ("pending_verification", "pending_admin_review", "more_information_requested")


def withdraw_sublet_request(db: Session, sublet_request: SubletRequest, user: "UserAccount") -> SubletRequest:
    """ZR-SUB-003 Section 6/13 permissions matrix: 'Withdraw pending request |
    Tenant.' Only the original requester, and only before a final decision --
    matches the doc's own state list (WITHDRAWN is reachable from any pending
    state, never from APPROVED/DECLINED/already-WITHDRAWN)."""
    guest = guest_crud.get_guest_for_user(db, user)
    if not guest or sublet_request.requested_by_guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only withdraw your own sublet request")
    if sublet_request.status not in _WITHDRAWABLE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A request in status '{sublet_request.status}' can no longer be withdrawn")

    sublet_request.status = "withdrawn"
    sublet_request.withdrawn_at = datetime.now(timezone.utc)

    listing = sublet_request.current_occupancy.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Sublet request withdrawn",
            message="The tenant withdrew their sublet request before you decided.",
            notification_type="sublet_request.withdrawn",
            related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
        )
    db.commit()
    db.refresh(sublet_request)
    return sublet_request


# ZR-SUB-003 Wireframe J: "The tenant must be able to access the record after
# the request is completed." A request still in flight has no decision to
# record yet -- these are the terminal states a download is meaningful for.
_COMPLETED_STATUSES = ("approved", "rejected", "withdrawn")


def generate_sublet_decision_record_pdf(db: Session, sublet_request: SubletRequest) -> bytes:
    """ZR-SUB-003 Section 7: 'Downloaded records should include request ID,
    property/rental reference, parties, dates, scope, conditions, decision
    authority, decision timestamp and integrity metadata.' Rendered on demand
    from the request's own immutable fields -- no separate persisted artifact
    needed, since the SubletRequest row itself never gets rewritten once
    decided (see the model's own 'no destructive state changes' discipline)."""
    if sublet_request.status not in _COMPLETED_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "This sublet request has not been completed yet")

    listing = sublet_request.current_occupancy.listing if sublet_request.current_occupancy else None
    requester = db.get(Guest, sublet_request.requested_by_guest_id) if sublet_request.requested_by_guest_id else None
    proposed_party = db.get(Party, sublet_request.proposed_renter_party_id)
    proposed_user = db.scalar(select(UserAccount).where(UserAccount.party_id == sublet_request.proposed_renter_party_id))

    if sublet_request.decided_by_user_id:
        decider = db.get(UserAccount, sublet_request.decided_by_user_id)
        authority = f"{decider.full_name} (verified Host)" if decider else "Verified Host"
    elif sublet_request.decided_by_admin_id:
        decider = db.get(AdminUser, sublet_request.decided_by_admin_id)
        authority = f"{decider.full_name} (Zoiko legal-ops override)" if decider else "Zoiko legal-ops override"
    else:
        authority = "Tenant withdrew before a decision" if sublet_request.status == "withdrawn" else "—"

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

    write("Zoiko Rooms -- Sublet Request Decision Record", size=16, bold=True, gap=10 * mm)
    write(f"Request ID: ZR-SUB-{sublet_request.id:06d}", size=10)
    write(f"Status: {sublet_request.status.replace('_', ' ').upper()}", size=10, gap=10 * mm)

    write("Property / Rental", size=12, bold=True)
    write(listing.name if listing else f"Occupancy #{sublet_request.current_occupancy_id}")
    write(listing.city if listing else "", gap=10 * mm)

    write("Parties", size=12, bold=True)
    write(f"Requesting tenant: {requester.name if requester else 'Unknown'}")
    write(f"Proposed occupant: {proposed_user.full_name if proposed_user else (proposed_party.id if proposed_party else 'Unknown')}", gap=10 * mm)

    write("Arrangement", size=12, bold=True)
    write(f"Type: {sublet_request.arrangement_type}")
    write(f"Requested: {sublet_request.created_at.strftime('%Y-%m-%d %H:%M UTC')}")
    if sublet_request.reason:
        write(f"Reason: {sublet_request.reason}", gap=10 * mm)
    else:
        y -= 3 * mm

    write("Decision", size=12, bold=True)
    write(f"Authority: {authority}")
    if sublet_request.decided_at:
        write(f"Decided: {sublet_request.decided_at.strftime('%Y-%m-%d %H:%M UTC')}")
    if sublet_request.withdrawn_at:
        write(f"Withdrawn: {sublet_request.withdrawn_at.strftime('%Y-%m-%d %H:%M UTC')}")
    if sublet_request.admin_notes:
        write(f"Notes: {sublet_request.admin_notes}")
    if sublet_request.approval_conditions:
        write(f"Conditions: {sublet_request.approval_conditions}")
    if sublet_request.approval_expires_at:
        write(f"Approval expires: {sublet_request.approval_expires_at.strftime('%Y-%m-%d')}")
    y -= 3 * mm

    pdf.setFont("Helvetica-Oblique", 7)
    pdf.drawString(x, 15 * mm, "Zoiko Rooms routed and recorded this decision; it did not grant permission on the owner's behalf.")
    pdf.save()
    return buffer.getvalue()


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
