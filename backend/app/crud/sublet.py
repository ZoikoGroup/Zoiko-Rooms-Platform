from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.admin_user import AdminUser
from app.models.authority_record import AuthorityRecord
from app.models.finance import OBLIGATION_TYPE_TO_PLANE, Obligation
from app.models.leasing import Agreement, AgreementVersion, Application, Offer, OfferTerms
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.sublet_request import (
    CO_TENANCY_ARRANGEMENT_TYPES,
    NO_TENANCY_ARRANGEMENT_TYPES,
    REPLACING_ARRANGEMENT_TYPES,
    SHARED_ROOM_ARRANGEMENT_TYPES,
    SUBLET_ARRANGEMENT_TYPES,
    SUBLET_DECLINE_REASON_CODES,
    SubletRequest,
)
from app.models.user_account import UserAccount
from app.models.guest import Guest
from app.crud import guest as guest_crud
from app.crud import notification as notif_crud
from app.core.security import verify_password
from app.crud.authority import get_valid_authority_for_room
from app.crud.eligibility import check_room_capacity
from app.crud.ids import new_id
from app.crud.market_policy import jurisdiction_code_for_occupancy, resolve_market_policy, to_policy_snapshot
from app.crud.party import party_id_for_listing
from app.schemas.leasing import SubletChronologyEvent, SubletRequestRead

ACTIVE_SUBLET_REQUEST_STATUSES = (
    "draft",
    "pending_verification",
    "pending_admin_review",
    "more_information_requested",
    "tenant_response_submitted",
    "awaiting_co_tenant",
)

# ZR-SUB-003 Section 6: the states a Host/Admin can actually decide from --
# approve/reject/request-info all gate on this instead of a single hard-coded
# status, now that a tenant's response lands in its own distinct state.
DECIDABLE_SUBLET_REQUEST_STATUSES = ("pending_admin_review", "tenant_response_submitted")


def to_sublet_request_read(db: Session, sr: SubletRequest) -> SubletRequestRead:
    """Enrich the bare record with the room/people context a reviewer actually
    needs -- 'occupancy #14 -> party #37' means nothing on its own."""
    occupancy = sr.current_occupancy
    listing = occupancy.listing if occupancy else None
    current_tenant = occupancy.guest if occupancy else None
    proposed_renter = (
        db.scalar(select(UserAccount).where(UserAccount.party_id == sr.proposed_renter_party_id))
        if sr.proposed_renter_party_id is not None
        else None
    )
    # While the request awaits a decision the reviewer sees the live risk
    # (what approving now would record); afterwards, what was recorded.
    if sr.status in DECIDABLE_SUBLET_REQUEST_STATUSES and occupancy is not None:
        risk_tier, risk_reason = evaluate_sublet_overlap(db, sr)
    else:
        risk_tier, risk_reason = sr.occupant_risk_tier, sr.occupant_risk_reason

    from app.crud.rental_message import host_thread_available

    new_agreement = sr.new_agreement if sr.new_agreement_id else None
    return SubletRequestRead(
        id=sr.id,
        current_occupancy_id=sr.current_occupancy_id,
        host_thread_available=host_thread_available(sr),
        new_application_id=new_agreement.offer.application_id if new_agreement is not None and new_agreement.offer else None,
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
        info_requested_document_types=sr.info_requested_document_types,
        info_request_due_at=sr.info_request_due_at,
        proposed_start_date=sr.proposed_start_date,
        proposed_end_date=sr.proposed_end_date,
        approval_conditions=sr.approval_conditions,
        approval_condition_list=sr.approval_condition_list,
        approved_with_authority_confirmation=sr.approved_with_authority_confirmation,
        approval_expires_at=sr.approval_expires_at,
        withdrawn_at=sr.withdrawn_at,
        reason=sr.reason,
        arrangement_type=sr.arrangement_type,
        requested_by_guest_id=sr.requested_by_guest_id,
        original_renter_liability=sr.original_renter_liability,
        new_occupant_liability=sr.new_occupant_liability,
        deposit_disposition=sr.deposit_disposition,
        proposed_monthly_rent=(sr.policy_snapshot or {}).get("proposed_monthly_rent"),
        proposed_deposit_amount=(sr.policy_snapshot or {}).get("proposed_deposit_amount"),
        policy_snapshot=sr.policy_snapshot,
        new_agreement_id=sr.new_agreement_id,
        payee_model=sr.payee_model,
        occupant_risk_tier=risk_tier,
        occupant_risk_reason=risk_reason,
        listing_name=listing.name if listing else "",
        property_id=occupancy.room.property_id if occupancy and occupancy.room else None,
        listing_city=listing.city if listing else "",
        room_type=listing.room_type if listing else "",
        guests=listing.guests if listing else 0,
        bedrooms=listing.bedrooms if listing else 0,
        bathrooms=listing.bathrooms if listing else 0,
        current_tenant_name=current_tenant.name if current_tenant else "",
        proposed_renter_name=proposed_renter.full_name if proposed_renter else "",
        version=sr.version,
        decline_reason_code=sr.decline_reason_code,
        superseded_by_sublet_request_id=sr.superseded_by_sublet_request_id,
        expired_at=sr.expired_at,
        cancelled_by_authority_at=sr.cancelled_by_authority_at,
        cancelled_by_authority_admin_id=sr.cancelled_by_authority_admin_id,
        cancelled_by_authority_reason=sr.cancelled_by_authority_reason,
        co_tenant_responded_at=sr.co_tenant_responded_at,
        ended_at=sr.ended_at,
        end_reason=sr.end_reason,
        shared_room_active=is_active_shared_room(sr),
        # Live, not as of submission -- either person's verification can lapse
        # while the room is shared.
        current_tenant_identity_verified=_guest_identity_verified(db, current_tenant),
        proposed_renter_identity_verified=_party_identity_verified(db, sr.proposed_renter_party_id),
    )


def _party_identity_verified(db: Session, party_id: int | None) -> bool:
    from app.crud.identity_verification import get_verified_identity_for_party

    return bool(party_id) and get_verified_identity_for_party(db, party_id) is not None


def _guest_identity_verified(db: Session, guest: Guest | None) -> bool:
    user = guest_crud.get_user_for_guest(db, guest) if guest is not None else None
    return _party_identity_verified(db, user.party_id if user else None)


def is_active_shared_room(sr: SubletRequest) -> bool:
    """An approved co-tenancy that still holds: not ended, and the renter's own
    tenancy (which the co-tenant lives under) is still live."""
    occupancy = sr.current_occupancy
    return (
        sr.arrangement_type in SHARED_ROOM_ARRANGEMENT_TYPES
        and sr.status == "approved"
        and sr.new_agreement_id is None
        and occupancy is not None
        and occupancy.status in ("PENDING_MOVE_IN", "ACTIVE")
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
    idempotency_key: str = "",
    proposed_start_date: date | None = None,
    proposed_end_date: date | None = None,
    proposed_deposit_amount: float | None = None,
) -> SubletRequest:
    """Current renter submits a sublet request."""
    if not user.party_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no associated party")

    # ZR-SUB-003 Section 3 Step 2 Wireframe B: both optional, but when given
    # must be a real range within the occupancy's own remaining lease --
    # never past the master tenancy's own end date (same DATE INVARIANT as
    # _create_co_tenancy_agreement's own term-length clamp below).
    if proposed_start_date is not None and proposed_end_date is not None and proposed_start_date >= proposed_end_date:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The proposed start date must be before the proposed end date")

    # ZR-SUB-003 Section 12: a retry with the same key returns the
    # already-created row rather than raising the "already exists" 409
    # below or creating a second one -- same idempotency_key convention as
    # crud/finance.py's SimulatedPayment/RefundRequest.
    if idempotency_key:
        existing_by_key = db.scalar(select(SubletRequest).where(SubletRequest.idempotency_key == idempotency_key))
        if existing_by_key:
            return existing_by_key

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

    if proposed_start_date is not None and proposed_start_date < date.today():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The proposed start date cannot be in the past")
    if proposed_end_date is not None and occupancy.expected_end_date and proposed_end_date > occupancy.expected_end_date:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"The proposed end date cannot be later than the tenancy's own end date ({occupancy.expected_end_date.isoformat()})",
        )

    # The host's own minimum-notice rule (Sublet Requests > Sublet policy settings).
    from app.crud.host_sublet_policy import get_policy as get_host_sublet_policy

    host_policy = get_host_sublet_policy(db, occupancy.room.property.owner_party_id)
    if host_policy.min_notice_enabled and proposed_start_date is not None:
        if (proposed_start_date - date.today()).days < host_policy.min_notice_days:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"This host needs sublet requests at least {host_policy.min_notice_days} days before the start date",
            )

    policy = resolve_market_policy(db, jurisdiction_code_for_occupancy(occupancy))

    # ZR-SUB-003 Section 8: sublet.maxDuration -- "Configured duration
    # constraints where applicable." Only checkable when the tenant actually
    # proposed both dates; a request with no dates has nothing to measure.
    if policy.sublet_max_duration_months and proposed_start_date is not None and proposed_end_date is not None:
        if _whole_months_between(proposed_start_date, proposed_end_date) > policy.sublet_max_duration_months:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"This jurisdiction limits a sublet arrangement to {policy.sublet_max_duration_months} months",
            )

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
        if proposed_deposit_amount is not None:
            # The incoming tenant's own deposit is whatever is agreed between the people involved -- including none --
            # but never more than the market's deposit cap on the rent they will actually pay.
            if proposed_deposit_amount < 0:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "The deposit can't be negative")
            effective_rent = proposed_monthly_rent if proposed_monthly_rent is not None else float(occupancy.offer.terms[-1].monthly_rent)
            max_deposit = round(effective_rent * float(policy.deposit_max_rent_multiple), 2)
            if proposed_deposit_amount > max_deposit:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Proposed deposit {proposed_deposit_amount:.2f} exceeds the maximum of {max_deposit:.2f} "
                    f"({policy.deposit_max_rent_multiple}x the monthly rent)",
                )

    # Proposed renter party must exist
    proposed_party = db.get(Party, proposed_renter_party_id)
    if not proposed_party:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proposed renter party not found")
    if not verify_sublet_identity(
        db, type("SubletIdentityCandidate", (), {"proposed_renter_party_id": proposed_renter_party_id})()
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Proposed renter must have an approved identity verification")

    # Check if a still-open draft/submitted request already exists.
    existing = db.scalar(
        select(SubletRequest).where(
            SubletRequest.current_occupancy_id == occupancy_id,
            SubletRequest.status.in_(ACTIVE_SUBLET_REQUEST_STATUSES),
        )
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sublet request already exists for this occupancy")

    sublet_request = SubletRequest(
        current_occupancy_id=occupancy_id,
        proposed_renter_party_id=proposed_renter_party_id,
        # Every request goes to the Host first. For a shared room the named person is asked only once the Host has
        # approved (status "awaiting_co_tenant"), and moves in only after they accept.
        status="pending_admin_review",
        authority_evidence_ref=authority_evidence_ref,
        arrangement_type=arrangement_type,
        reason=reason.strip(),
        idempotency_key=idempotency_key or None,
        proposed_start_date=proposed_start_date,
        proposed_end_date=proposed_end_date,
        # Captured now, before approval can overwrite occupancy.guest_id -- this
        # is the only reliable record of who actually requested this (ZR-ENG-CLR-003
        # Section 15.1's occupancy_relationship / audit_event requirement).
        requested_by_guest_id=occupancy.guest_id,
        policy_snapshot={
            **to_policy_snapshot(policy),
            "consent_standard": policy.sublet_consent_standard,
            "consent_response_days": policy.sublet_consent_response_days,
            "arrangement_permitted": True,
            # ZR-SUB-003 Section 8's own config keys -- frozen at submission
            # time, same "reproducible from the snapshot" discipline as
            # to_termination_policy_snapshot.
            "ui_term": policy.sublet_ui_term,
            "max_duration_months": policy.sublet_max_duration_months,
            "required_fields": policy.sublet_required_fields,
            "required_documents": policy.sublet_required_documents,
            "signature_mode": policy.sublet_signature_mode,
            "notice_requirements": policy.sublet_notice_requirements,
            "retention_class": policy.sublet_retention_class,
            "additional_gates": policy.sublet_additional_gates,
            **({"proposed_monthly_rent": proposed_monthly_rent} if proposed_monthly_rent is not None else {}),
            **({"proposed_deposit_amount": proposed_deposit_amount}
               if proposed_deposit_amount is not None and arrangement_type in CO_TENANCY_ARRANGEMENT_TYPES else {}),
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
    if sublet_request.status == "pending_admin_review":
        _notify_host_of_submission(db, sublet_request, user.full_name)

    # ZR-ENG-CLR-003 Section 17.2 notification matrix: the proposed occupant must
    # be told a request naming them exists, even before any approval -- previously
    # this platform never notified them at all, at any stage.
    # A shared-room co-tenant hears nothing yet: their one notification comes when the host has approved and it
    # is theirs to accept.
    if arrangement_type in SHARED_ROOM_ARRANGEMENT_TYPES:
        proposed_message = ""
    elif arrangement_type in CO_TENANCY_ARRANGEMENT_TYPES:
        proposed_message = f"{user.full_name} has requested to add you as a co-tenant on their room, pending the host's review."
    elif arrangement_type in NO_TENANCY_ARRANGEMENT_TYPES:
        proposed_message = f"{user.full_name} has requested permission for you to reside in their room, pending the host's review."
    else:
        proposed_message = f"{user.full_name} has requested to hand over their room to you, pending the host's review."
    if proposed_message:
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


def _notify_host_of_submission(db: Session, sublet_request: SubletRequest, requester_name: str) -> None:
    listing = sublet_request.current_occupancy.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="A sublet was requested for your listing",
            message=f'{requester_name} requested to sublet "{listing.name}". Review it in your Sublet Requests dashboard.',
            notification_type="sublet_request.submitted",
            related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
        )


def create_draft_sublet_request(db: Session, user: UserAccount, occupancy_id: int) -> SubletRequest:
    """Create the tenant's unsubmitted draft for Step 1 of ZR-SUB-003.

    A draft is visible only to the tenant. It is not routed to the Host and
    carries no proposed occupant yet, so it cannot be decided until a later
    submit step fills the required facts and moves it to pending review.
    """
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")
    _assert_sublet_permitted(db, occupancy)

    existing = db.scalar(
        select(SubletRequest).where(
            SubletRequest.current_occupancy_id == occupancy_id,
            SubletRequest.status.in_(ACTIVE_SUBLET_REQUEST_STATUSES),
        )
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sublet request already exists for this occupancy")

    sublet_request = SubletRequest(
        current_occupancy_id=occupancy_id,
        proposed_renter_party_id=None,
        status="draft",
        requested_by_guest_id=occupancy.guest_id,
        policy_snapshot={
            **to_policy_snapshot(resolve_market_policy(db, jurisdiction_code_for_occupancy(occupancy))),
            "draft": True,
        },
    )
    db.add(sublet_request)
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
            .where(Listing.party_id == user.party_id, SubletRequest.status != "draft")
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
    """Check if proposed renter has verified identity -- the same account-level
    gate applying for a room uses."""
    return _party_identity_verified(db, sublet_request.proposed_renter_party_id)


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


def _sublet_agreement_snapshot(db: Session, offer: Offer, *, signed_on_approval: bool) -> dict:
    """The frozen document snapshot for a sublet's own agreement -- the same
    one an ordinary agreement gets (crud/leasing.py:_build_agreement_snapshot)
    when the room has an agreement profile, so its executed PDF can be
    rendered once payment confirms it. Without a profile, the fields the PDF
    itself needs, taken from the sublet's own offer."""
    from app.crud.leasing import _build_agreement_snapshot
    from app.services.agreement_profile import resolve_agreement_profile

    listing = offer.listing
    terms = offer.terms[-1]
    extra = {"source": "sublet_co_tenancy", "signed_on_approval": signed_on_approval}
    profile = resolve_agreement_profile(db, listing, listing.room)
    if profile is not None:
        return {**_build_agreement_snapshot(offer, profile, latest_terms=terms), **extra}
    guest = offer.guest
    return {
        "listing_id": listing.id, "listing_name": listing.name, "listing_location": listing.location,
        "listing_city": listing.city, "room_size": listing.room.size if listing.room else None,
        "room_has_ensuite": listing.room.has_ensuite if listing.room else None,
        "provider_name": listing.contact_name or (listing.owner.full_name if listing.owner else "Host"),
        "provider_email": listing.contact_email or (listing.owner.email if listing.owner else ""),
        "renter_name": guest.name if guest else "", "renter_email": guest.email if guest else "",
        "renter_phone": guest.phone if guest else "",
        "monthly_rent": float(terms.monthly_rent), "deposit_amount": float(terms.deposit_amount),
        "start_date": terms.start_date.isoformat(), "term_months": terms.term_months,
        "clause_ids": [], "required_signers": ["provider", "renter"], **extra,
    }


def _create_co_tenancy_agreement(
    db: Session, occupancy: Occupancy, proposed_guest: Guest, monthly_rent_override: float | None = None,
    *, require_e_signature: bool = False, recipient_party_id: int | None = None,
    deposit_override: float | None = None,
) -> Agreement:
    """SUBLEASE_PARTIAL/ADD_CO_TENANT: builds a real, independent Application ->
    Offer -> Terms -> Agreement chain for the co-tenant, rather than overwriting
    current_occupancy, which stays exactly as-is. The co-tenant then pays and
    moves in on this agreement exactly like any other tenant; room capacity is
    enforced at that move-in step by the same check_room_capacity gate every
    occupancy goes through. monthly_rent_override is the requester's negotiated
    rent (already validated against the market-pack cap at submission time);
    absent that, mirrors the existing tenant's rent unchanged.

    require_e_signature (ZR-SUB-003 Section 5.2/8 sublet.signatureMode ==
    E_SIGNATURE, opt-in per jurisdiction -- default False preserves the
    original auto-signed behavior exactly): when True, the agreement is
    left SENT with a real AgreementVersion instead of pre-marked SIGNED, so
    the Host and co-tenant must actually sign it themselves via the
    existing crud/leasing.py:host_sign_agreement/user_sign_agreement (the
    same functions every ordinary tenancy's agreement already goes
    through) before it executes. This also fixes a latent bug this
    investigation surfaced: the auto-signed shortcut never called
    _ensure_pending_move_in_occupancy, so a co-tenant approved this way
    could never actually receive an Occupancy row through any existing
    code path -- routing through real _apply_signature fixes that for
    free, since that function calls it unconditionally once both parties
    sign and initial obligations are paid."""
    listing = occupancy.listing
    latest_terms = occupancy.offer.terms[-1]
    monthly_rent = monthly_rent_override if monthly_rent_override is not None else latest_terms.monthly_rent
    # The incoming tenant's own deposit: what was agreed (0 = none), else the original tenancy's.
    deposit_amount = deposit_override if deposit_override is not None else latest_terms.deposit_amount
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
        monthly_rent=monthly_rent, deposit_amount=deposit_amount,
        start_date=start_date, term_months=term_months,
    ))
    db.flush()

    if require_e_signature:
        agreement = Agreement(offer_id=offer.id, status="SENT")
        db.add(agreement)
        db.flush()
        db.add(AgreementVersion(
            agreement_id=agreement.id, version_no=1, status="WORKING",
            snapshot=_sublet_agreement_snapshot(db, offer, signed_on_approval=False),
        ))
        db.flush()
    else:
        # Signed by both on approval, but -- like every other tenancy -- it only
        # becomes SIGNED (and gets its move-in record) once the deposit and first
        # rent are recorded as paid. Marking it SIGNED here skipped that, so the
        # subtenant never got an occupancy and their later months never appeared.
        from app.crud.leasing import _payment_deadline_after_signing

        now = datetime.now(timezone.utc)
        agreement = Agreement(
            offer_id=offer.id, status="PAYMENT_IN_PROGRESS", signature_ref=new_id("SIG"),
            signed_by_provider_at=now, signed_by_renter_at=now,
        )
        db.add(agreement)
        db.flush()
        # Same document version the e-signature path creates -- it's what gets
        # frozen into the executed PDF once payment confirms the agreement.
        db.add(AgreementVersion(
            agreement_id=agreement.id, version_no=1, status="WORKING",
            snapshot=_sublet_agreement_snapshot(db, offer, signed_on_approval=True),
        ))
        db.flush()
        agreement.offer = offer
        agreement.payment_session_expires_at = _payment_deadline_after_signing(agreement, now)

    # The sublet's own terms: the negotiated rent, due from the sublet's start
    # (today) -- not the original tenancy's rent and long-past start date.
    db.add(Obligation(
        obligation_type="RENT", money_plane=OBLIGATION_TYPE_TO_PLANE["RENT"],
        amount=monthly_rent, currency=listing.currency, due_date=start_date, agreement_id=agreement.id,
    ))
    if float(deposit_amount) > 0:
        db.add(Obligation(
            obligation_type="DEPOSIT", money_plane=OBLIGATION_TYPE_TO_PLANE["DEPOSIT"],
            amount=deposit_amount, currency=listing.currency, due_date=start_date, agreement_id=agreement.id,
        ))
    db.flush()

    # ZR-PAY-002 Section 4/6: the record/evidence-layer counterpart to the two
    # custody-based Obligation rows above -- same best-effort placement as
    # crud/leasing.py:create_agreement's own equivalent block (this co-tenancy
    # agreement is otherwise identical in shape to an ordinary one, just
    # created programmatically instead of through the applications flow).
    try:
        from app.crud.rental_payment import create_obligation as create_rental_payment_obligation
        from app.crud.rental_payment import resolve_rent_recipient_party_id

        # The caller decides who's paid (the original renter for a sublease/
        # lodger, the host for a co-tenant); the host is the fallback.
        if recipient_party_id is None:
            recipient_party_id = resolve_rent_recipient_party_id(db, listing.room) if listing.room else None
        if recipient_party_id is not None:
            create_rental_payment_obligation(
                db, obligation_type="RENT", tenant_guest_id=proposed_guest.id, recipient_party_id=recipient_party_id,
                amount=monthly_rent, currency=listing.currency, due_date=start_date,
                agreement_id=agreement.id,
            )
            if float(deposit_amount) > 0:
                create_rental_payment_obligation(
                    db, obligation_type="DEPOSIT", tenant_guest_id=proposed_guest.id, recipient_party_id=recipient_party_id,
                    amount=deposit_amount, currency=listing.currency, due_date=start_date,
                    agreement_id=agreement.id,
                )
    except Exception:
        pass

    return agreement


def _assert_can_decide_sublet(db: Session, sublet_request: SubletRequest, actor: UserAccount) -> None:
    """ZR-SUB-003 IMPLEMENTATION LOCK: 'A tenant's request for permission to
    sublet must be sent to the verified landlord, agent or other authorized
    property representative. Zoiko Rooms records and routes the request; it
    does not grant permission on the owner's behalf.' Approval, decline and
    requests for more information stay between the Host (the party that owns
    the listing) and the renter -- Zoiko staff, super admins included, never
    decide one."""
    if not isinstance(actor, UserAccount):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sublet requests are decided by the host")
    listing = sublet_request.current_occupancy.listing
    if not actor.party_id or party_id_for_listing(listing) != actor.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only decide sublet requests for your own listings")
    _assert_current_decision_authority(db, listing.room_id)


def _assert_step_up_password(actor: UserAccount, step_up_password: str) -> None:
    """ZR-SUB-003 Section 10 step-up authentication -- see
    approve_sublet_request's own docstring for exactly when this is called.
    Re-verifies the deciding actor's own current password; there is no
    real MFA/authenticator-app provider anywhere in this codebase (an
    honest, platform-wide gap -- see Section 10's own field docstring on
    SubletRequestDecision), so a fresh password entry is this build's real
    step-up factor, same as many real systems' "re-enter your password to
    confirm" pattern for a sensitive action."""
    if not step_up_password:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Re-enter your password to confirm this irreversible handover (step-up authentication required)",
        )
    if not verify_password(step_up_password, actor.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect password -- could not confirm this decision")


def _assert_current_decision_authority(db: Session, room_id: int) -> None:
    """ZR-SUB-003 Section 5/'AUTHORITY GATE': 'enabled only for a user whose
    authority for the relevant property/rental is verified and current. An
    agent whose authority has expired or been revoked must not be able to
    decide the request.' Reuses the same crud.authority.get_valid_authority_
    for_room gate crud/listing.py already enforces at publish/move-in time
    (services/eligibility.py:jurisdiction_gates_pass) -- this is simply one
    more re-check point on the same real record, per that gate's own "re-
    checked at every pipeline stage" doctrine.
    Only enforced once at least one AuthorityRecord has ever been submitted
    for the room: a room published before this check existed (or in a test
    fixture that never modeled authority at all) has no such record yet,
    and retroactively hard-blocking every sublet decision for it would make
    the feature unusable rather than catch a real lapse. Once a room has at
    least one AuthorityRecord, though, it must be a currently valid one."""
    has_any_authority_record = db.scalar(select(AuthorityRecord.id).where(AuthorityRecord.room_id == room_id).limit(1))
    if not has_any_authority_record:
        return
    if not get_valid_authority_for_room(db, room_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This room's decision authority has expired or been revoked -- it must be re-verified before a sublet request can be decided",
        )


def _reassign_open_rent_to(db: Session, occupancy: Occupancy, guest_id: str) -> None:
    """On a full assignment, move the tenancy's open rent/deposit obligations
    with no payment recorded against them to the assignee -- anything the
    original renter already paid or recorded stays theirs."""
    from sqlalchemy import or_

    from app.models.rental_payment import RentalPaymentObligation

    agreement = occupancy.offer.agreement if occupancy.offer is not None else None
    scopes = [RentalPaymentObligation.occupancy_id == occupancy.id]
    if agreement is not None:
        scopes.append(RentalPaymentObligation.agreement_id == agreement.id)
    for obligation in db.scalars(select(RentalPaymentObligation).where(or_(*scopes))):
        if obligation.status in ("UPCOMING", "DUE", "OVERDUE") and not obligation.records:
            obligation.tenant_guest_id = guest_id


def evaluate_sublet_overlap(db: Session, sublet_request: SubletRequest) -> tuple[str, str]:
    """ZR-ENG-CLR-001 Rule 6 / Section 9 for the incoming occupant: the same
    risk tier crud/leasing.py:_accept_offer_and_hold_room computes when an
    offer is accepted, so taking a tenancy over by sublet can't skip the
    check an ordinary application gets. The window is what they'd actually
    occupy -- the proposed dates, else from today to the end of the current
    tenancy. ADDITIONAL_OCCUPANT creates no tenancy, so never overlaps.
    Read-only: safe to call while just displaying a pending request."""
    if sublet_request.arrangement_type in NO_TENANCY_ARRANGEMENT_TYPES or sublet_request.proposed_renter_party_id is None:
        return "NONE", ""
    occupancy = sublet_request.current_occupancy
    user = db.scalar(
        select(UserAccount)
        .where(UserAccount.party_id == sublet_request.proposed_renter_party_id, UserAccount.is_active.is_(True))
        .order_by(UserAccount.id)
    )
    if user is None:
        return "NONE", ""
    guest = db.scalar(select(Guest).where(Guest.user_account_id == user.id)) or db.scalar(
        select(Guest).where(Guest.email == user.email, Guest.user_account_id.is_(None))
    )
    if guest is None:
        return "NONE", ""  # never rented anything, so nothing to overlap with

    start = sublet_request.proposed_start_date or date.today()
    end = sublet_request.proposed_end_date or occupancy.expected_end_date
    if end is None and occupancy.offer is not None and occupancy.offer.terms:
        terms = occupancy.offer.terms[-1]
        end = _add_months(terms.start_date, terms.term_months)
    if end is None or end <= start:
        return "NONE", ""

    from app.services.overlap import evaluate_occupant_overlap

    return evaluate_occupant_overlap(
        db, occupant_guest_id=guest.id, listing_id=occupancy.listing_id, start_date=start, end_date=end,
    )


def _add_assignee_agreement_version(db: Session, sublet_request: SubletRequest) -> AgreementVersion | None:
    """ASSIGNMENT_FULL: the agreement now names the incoming tenant. Adds a new
    version of the same agreement -- every term copied unchanged from the
    current version, only the renter details rebuilt for the assignee -- whose
    PDF is rendered and frozen once the approval commits (see
    approve_sublet_request). No new signing and no new payments: the open rent
    has already moved to the assignee and the deposit stays with the tenancy.
    The previous version stays on file as the original renter's record."""
    import copy

    from app.services.agreement_document.facts import build_document_facts

    offer = sublet_request.current_occupancy.offer
    agreement = offer.agreement
    if agreement is None or not agreement.versions or not offer.terms:
        return None
    db.flush()
    db.refresh(offer)  # offer.guest is now the assignee
    guest = offer.guest
    current = agreement.versions[-1]
    snapshot = copy.deepcopy(current.snapshot)
    previous_renter = snapshot.get("renter_name", "")

    snapshot["renter_name"] = guest.name if guest else ""
    snapshot["renter_email"] = guest.email if guest else ""
    snapshot["renter_phone"] = guest.phone if guest else ""
    if isinstance(snapshot.get("document"), dict):
        snapshot["document"]["renter"] = build_document_facts(offer, offer.terms[-1])["renter"]
    snapshot["assignment"] = {
        "sublet_request_id": sublet_request.id,
        "previous_renter_name": previous_renter,
        "previous_version_no": current.version_no,
        "assigned_at": datetime.now(timezone.utc).isoformat(),
    }

    version = AgreementVersion(
        agreement_id=agreement.id, version_no=current.version_no + 1, status="WORKING", snapshot=snapshot,
    )
    db.add(version)
    agreement.version = version.version_no
    db.flush()
    return version


def approve_sublet_request(
    db: Session, sublet_request: SubletRequest, actor: UserAccount, notes: str = "",
    conditions: str = "", expires_at: datetime | None = None,
    condition_list: list[str] | None = None, authority_confirmed: bool = False, step_up_password: str = "",
    override_reason: str = "",
) -> SubletRequest:
    """The verified Host approves a sublet request (only the Host -- see
    _assert_can_decide_sublet).
    ZR-SUB-003 Section 5.2: conditions/expires_at are optional, descriptive
    terms attached to the approval (see the model's own note on why expiry
    isn't automatically enforced). authority_confirmed mirrors the
    wireframe's own confirmation checkbox -- recorded as part of the
    decision evidence, but not hard-required at the crud layer (the real
    authority check is _assert_can_decide_sublet above; this is UX
    reinforcement, not a second security gate, and making it a hard 400
    would break every existing caller that predates this field).
    step_up_password is Section 10's own step-up authentication -- required
    (and re-verified against the deciding actor's own account password)
    only when approving a REPLACING_ARRANGEMENT_TYPES request, the one real
    "risk signal" already in this taxonomy: an irreversible full handover of
    the tenancy, unlike a lower-stakes co-tenancy/additional-occupant
    approval.
    override_reason: only consulted when the incoming occupant's overlap
    check (evaluate_sublet_overlap) comes back BLOCK -- the approver's
    recorded exception, exactly like user_accept_offer's own."""
    _assert_can_decide_sublet(db, sublet_request, actor)
    assignee_version = None
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Approval expiry must be in the future")
    if sublet_request.arrangement_type in REPLACING_ARRANGEMENT_TYPES:
        _assert_step_up_password(actor, step_up_password)

    if sublet_request.status not in DECIDABLE_SUBLET_REQUEST_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only pending sublet requests can be approved")
    if not verify_sublet_identity(db, sublet_request):
        raise HTTPException(status.HTTP_409_CONFLICT, "Proposed renter no longer has an approved identity verification")
    _assert_sublet_permitted(db, sublet_request.current_occupancy)

    risk_tier, risk_reason = evaluate_sublet_overlap(db, sublet_request)
    if risk_tier == "BLOCK" and not override_reason.strip():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": "The proposed renter already has an overlapping tenancy elsewhere", "reason": risk_reason},
        )
    sublet_request.occupant_risk_tier = risk_tier
    sublet_request.occupant_risk_reason = (
        f"{risk_reason} (approved with override: {override_reason.strip()})" if risk_tier == "BLOCK" else risk_reason
    )[:500]

    listing = sublet_request.current_occupancy.listing
    proposed_guest = _guest_for_proposed_party(db, sublet_request.proposed_renter_party_id)
    is_shared_room = sublet_request.arrangement_type in SHARED_ROOM_ARRANGEMENT_TYPES
    is_co_tenancy = sublet_request.arrangement_type in CO_TENANCY_ARRANGEMENT_TYPES and not is_shared_room
    is_no_tenancy = sublet_request.arrangement_type in NO_TENANCY_ARRANGEMENT_TYPES
    policy = resolve_market_policy(db, jurisdiction_code_for_occupancy(sublet_request.current_occupancy))

    if is_shared_room:
        # ADD_CO_TENANT: the co-tenant lives under the renter's own tenancy and
        # pays the renter directly -- no agreement, obligation or occupancy of
        # their own, and the renter stays fully responsible to the Host.
        capacity_reasons = check_room_capacity(db, sublet_request.current_occupancy.room)
        if capacity_reasons:
            raise HTTPException(status.HTTP_409_CONFLICT, {"message": "Room cannot accept a co-tenant", "reasons": capacity_reasons})
        sublet_request.original_renter_liability = "ACTIVE"
        sublet_request.new_occupant_liability = "NONE"
        sublet_request.deposit_disposition = "NOT_APPLICABLE"
        sublet_request.payee_model = "EXTERNAL_PAYEE_RECORDED"
        requester_guest_id = sublet_request.current_occupancy.guest_id
    elif is_no_tenancy:
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
        # Who the new occupant pays: a subtenant/lodger contracts with the
        # original renter, so under the market's ORIGINAL_RENTER_PAYEE model
        # they pay that renter (who keeps paying the host); a co-tenant is the
        # host's own joint tenant and pays the host.
        from app.crud.rental_payment import SUBLET_ARRANGEMENTS_PAID_TO_ORIGINAL_RENTER, original_renter_party_id

        payee_model = policy.sublet_sublease_payee_model
        recipient_party_id = None
        if (
            sublet_request.arrangement_type in SUBLET_ARRANGEMENTS_PAID_TO_ORIGINAL_RENTER
            and payee_model == "ORIGINAL_RENTER_PAYEE"
        ):
            recipient_party_id = original_renter_party_id(db, sublet_request.current_occupancy)
            if recipient_party_id is None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "The original renter has no account to receive the subtenant's rent",
                )
        new_agreement = _create_co_tenancy_agreement(
            db, sublet_request.current_occupancy, proposed_guest, negotiated_rent,
            require_e_signature=(policy.sublet_signature_mode == "E_SIGNATURE"),
            recipient_party_id=recipient_party_id,
            deposit_override=sublet_request.policy_snapshot.get("proposed_deposit_amount"),
        )
        sublet_request.new_agreement_id = new_agreement.id
        new_agreement.offer.occupant_risk_tier = sublet_request.occupant_risk_tier
        new_agreement.offer.occupant_risk_reason = sublet_request.occupant_risk_reason
        # Both tenants remain fully active and liable -- adding a co-tenant
        # doesn't release the original renter of anything. A licensee/lodger
        # has no tenancy rights (Section 3's canonical meaning), unlike a true
        # co-tenant/sublease occupant who shares equal standing -- hence the
        # distinct liability outcome despite reusing the same agreement path.
        sublet_request.original_renter_liability = "ACTIVE"
        sublet_request.new_occupant_liability = (
            "LICENSEE" if sublet_request.arrangement_type == "LODGER_OR_LICENSEE" else "JOINT"
        )
        sublet_request.deposit_disposition = (
            "NO_SEPARATE_DEPOSIT" if sublet_request.policy_snapshot.get("proposed_deposit_amount") == 0
            else "SEPARATE_DEPOSIT_CREATED"
        )
        # ZR-ENG-CLR-003 Rule 4.5: the co-tenant funds their own agreement
        # directly -- there's no third party routing their rent through the
        # original tenant. This is the sublease/co-tenancy payee model
        # (market_policy.py's sublet_sublease_payee_model), distinct from
        # sublet_assignment_payee_model below -- an assignment hands the
        # *entire existing* tenancy's payment relationship to one new
        # occupant, while a co-tenancy/sublease creates an independent one
        # alongside the original.
        sublet_request.payee_model = payee_model
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
            sublet_request.current_occupancy.offer.occupant_risk_tier = sublet_request.occupant_risk_tier
            sublet_request.current_occupancy.offer.occupant_risk_reason = sublet_request.occupant_risk_reason
            # The released renter owes nothing more -- rent that's open and
            # untouched (nothing recorded on it yet) now belongs to the assignee.
            _reassign_open_rent_to(db, sublet_request.current_occupancy, proposed_guest.id)
            assignee_version = _add_assignee_agreement_version(db, sublet_request)
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
    sublet_request.approval_condition_list = [c.strip() for c in (condition_list or []) if c.strip()]
    sublet_request.approval_expires_at = expires_at
    sublet_request.approved_with_authority_confirmation = authority_confirmed
    sublet_request.decided_by_user_id = actor.id
    sublet_request.decided_at = datetime.now(timezone.utc)
    db.flush()
    if is_shared_room:
        # The Host's yes is the only approval: the co-tenant is in the room and gets one notification.
        _finalize_shared_room(db, sublet_request, proposed_guest, requester_guest_id)
        return sublet_request
    # ZR-SUBLET-PAY-003: who receives each payment, where the deposit goes and
    # how it's paid (bank / UPI / cash) -- derived from the approval.
    from app.services.sublet_payments import on_sublet_approved

    on_sublet_approved(db, sublet_request)

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
        if sublet_request.payee_model == "ORIGINAL_RENTER_PAYEE":
            requester = db.get(Guest, requester_guest_id)
            if requester is not None:
                notif_crud.notify_user_by_guest(
                    db, requester, title="You'll receive your subtenant's rent",
                    message=(
                        "Your subtenant pays their rent and deposit to you directly. Add your payment instructions "
                        "(bank, UPI or cash) in Payments so they know how to pay you, then mark their payments as "
                        "received there. You keep paying your own rent to your host as before."
                    ),
                    notification_type="sublet_request.payee_setup",
                    related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
                )
        next_step = (
            "Sign your agreement, then complete payment to move in."
            if policy.sublet_signature_mode == "E_SIGNATURE"
            else "Complete payment to move in."
        ) + " You can message your host from My Applications."
        notif_crud.notify_user_by_guest(
            db, proposed_guest,
            title="You've been added as a co-tenant",
            message=f"Your co-tenancy for occupancy #{sublet_request.current_occupancy_id} has been approved. {next_step}",
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
    if not is_no_tenancy and not is_shared_room:
        notif_crud.notify_user_by_guest(
            db, proposed_guest,
            title="You've been authorized as a new occupant",
            message=f'You have been approved to take over occupancy of "{listing.name}".' if listing else "You have been approved to take over an occupancy.",
            notification_type="sublet_request.authorized",
            related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
        )
    if listing and listing.party_id and not is_shared_room:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Occupant changed via sublet",
            message=f'The occupant of "{listing.name}" has changed following an approved sublet request.',
            notification_type="sublet_request.tenant_changed",
            related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
        )

    db.commit()
    if assignee_version is not None:
        # The assignee's version of the agreement, rendered and frozen like an
        # executed agreement -- what both parties now download.
        from app.crud.leasing import freeze_agreement_version

        freeze_agreement_version(db, sublet_request.current_occupancy.offer.agreement)
    db.refresh(sublet_request)
    return sublet_request


def _finalize_shared_room(db: Session, sublet_request: SubletRequest, proposed_guest: Guest, requester_guest_id: str | None) -> None:
    """The Host approved: the named person is in the room. Records who pays what and tells everyone how it
    works -- the co-tenant gets one notification, with their share and how to pay it."""
    from app.services.sublet_payments import on_sublet_approved

    listing = sublet_request.current_occupancy.listing
    sublet_request.status = "approved"
    db.flush()
    on_sublet_approved(db, sublet_request)
    _notify_sublet_host(db, sublet_request.current_occupancy, sublet_request.id, approved=True)
    from app.services.shared_room_payments import agreed_rent

    share = agreed_rent(sublet_request)
    how_to_pay = (
        f"You pay your {share:,.2f} share to the renter directly each month and record each payment under "
        "Rent & Deposit Payments; the renter confirms it there. The room's own rent is in your Sublets page."
        if share is not None else
        "You pay your share to the renter directly. The room's rent and payments are in your Sublets page."
    )
    notif_crud.notify_user_by_guest(
        db, proposed_guest,
        title="You're now sharing the room",
        message=f'The host approved you as a co-tenant of "{listing.name if listing else "this room"}". {how_to_pay}',
        notification_type="sublet_request.co_tenant_approved",
        related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
    )
    requester = db.get(Guest, requester_guest_id)
    if requester is not None:
        notif_crud.notify_user_by_guest(
            db, requester, title="Your co-tenant was approved",
            message=f"The host approved {proposed_guest.name or 'your co-tenant'}. They are now sharing the room with you.",
            notification_type="sublet_request.co_tenant_approved_requester",
            related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
        )
    if share is not None:
        requester = db.get(Guest, requester_guest_id)
        if requester is not None:
            notif_crud.notify_user_by_guest(
                db, requester, title="You'll receive your co-tenant's rent share",
                message=(
                    "Your co-tenant pays their share to you directly. Add your payment instructions (bank, UPI or "
                    "cash) in Payments so they know how to pay you, then confirm each payment there. You keep "
                    "paying your own rent to your host as before."
                ),
                notification_type="sublet_request.payee_setup",
                related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
            )
    db.commit()
    db.refresh(sublet_request)
    # The co-tenant's monthly rent share to the renter becomes a payment record. Best effort: a failure here must
    # never undo the acceptance itself.
    try:
        from app.services.shared_room_payments import sync_shared_room_rent

        sync_shared_room_rent(db, sublet_request)
    except Exception:
        db.rollback()


def reject_sublet_request(
    db: Session, sublet_request: SubletRequest, actor: UserAccount, notes: str = "", reason_code: str = "",
) -> SubletRequest:
    """The verified Host declines a sublet request (only the Host -- see
    _assert_can_decide_sublet).
    ZR-SUB-003 Section 5.3 FAIRNESS CONTROL: reason_code must be one of
    models.sublet_request.SUBLET_DECLINE_REASON_CODES; OTHER additionally
    requires a non-blank `notes` explanation."""
    _assert_can_decide_sublet(db, sublet_request, actor)

    if sublet_request.status not in DECIDABLE_SUBLET_REQUEST_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only pending sublet requests can be rejected")
    if reason_code and reason_code not in SUBLET_DECLINE_REASON_CODES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unrecognized decline reason code '{reason_code}'")
    if reason_code == "OTHER" and not notes.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "An explanation is required when the decline reason is 'OTHER'")
    sublet_request.status = "rejected"
    sublet_request.admin_decision = "rejected"
    sublet_request.admin_notes = notes
    sublet_request.decline_reason_code = reason_code
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
    db: Session, sublet_request: SubletRequest, actor: UserAccount, note: str,
    requested_document_types: list[str] | None = None, due_at: datetime | None = None,
) -> SubletRequest:
    """ZR-SUB-003 Section 5.1: 'MORE_INFORMATION_REQUESTED | Recipient needs
    additional information | Landlord/Agent.' The same decision authority as
    approve/reject (the Host only). requested_
    document_types/due_at are Wireframe G's own fields -- descriptive only
    (no upload system to actually require a type against, and no scheduler
    to enforce the due date -- same honest caveat as approval_expires_at)."""
    _assert_can_decide_sublet(db, sublet_request, actor)
    if sublet_request.status not in DECIDABLE_SUBLET_REQUEST_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a pending sublet request can have more information requested")
    if not note.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Describe what information you need from the tenant")
    if due_at is not None and due_at <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The response due date must be in the future")

    sublet_request.status = "more_information_requested"
    sublet_request.info_request_note = note.strip()
    sublet_request.info_requested_at = datetime.now(timezone.utc)
    sublet_request.info_requested_document_types = [t.strip() for t in (requested_document_types or []) if t.strip()]
    sublet_request.info_request_due_at = due_at

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
    # ZR-SUB-003 Section 6: TENANT_RESPONSE_SUBMITTED -- distinct from
    # pending_admin_review so the Host's queue can tell "never looked at
    # this yet" apart from "already asked a question, tenant just answered."
    # Both are decidable (DECIDABLE_SUBLET_REQUEST_STATUSES).
    sublet_request.status = "tenant_response_submitted"

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
_WITHDRAWABLE_STATUSES = (
    "draft", "pending_verification", "pending_admin_review", "more_information_requested", "tenant_response_submitted",
    "awaiting_co_tenant",
)


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
_COMPLETED_STATUSES = (
    "approved", "rejected", "withdrawn", "expired", "superseded", "cancelled_by_authority",
    "declined_by_co_tenant", "ended",
)


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

    from app.services.sublet_decision_document import (
        SubletDecisionModel, content_fingerprint, generated_at_label, render_sublet_decision_pdf,
    )

    occupancy = sublet_request.current_occupancy
    snapshot = sublet_request.policy_snapshot or {}
    currency = listing.currency if listing else ""
    arrangement = sublet_request.arrangement_type
    is_shared_room = arrangement in SHARED_ROOM_ARRANGEMENT_TYPES
    proposed_name = proposed_user.full_name if proposed_user else (f"Party #{proposed_party.id}" if proposed_party else "—")

    def when(value) -> str:
        return value.strftime("%d %b %Y, %H:%M UTC") if value else "—"

    def day(value) -> str:
        return value.strftime("%d %b %Y") if value else "—"

    def verified(flag: bool) -> str:
        return "Verified" if flag else "Not verified"

    summary_rows = [
        ("Decision", _DECISION_LABELS.get(sublet_request.status, sublet_request.status.replace("_", " ").title())),
        ("Decided by", authority),
        ("Decision date", when(sublet_request.decided_at or sublet_request.withdrawn_at or sublet_request.co_tenant_responded_at)),
        ("Request submitted", when(sublet_request.created_at)),
    ]
    if sublet_request.approval_expires_at:
        summary_rows.append(("Approval valid until", day(sublet_request.approval_expires_at)))
    if sublet_request.decline_reason_code:
        summary_rows.append(("Decline reason", sublet_request.decline_reason_code.replace("_", " ").capitalize()))
    if sublet_request.status == "approved" or sublet_request.status == "ended":
        summary_rows.append(("Authority confirmed by host", "Yes" if sublet_request.approved_with_authority_confirmation else "Not recorded"))

    property_rows = [
        ("Listing", listing.name if listing else "—"),
        ("City", listing.city if listing else "—"),
        ("Room type", listing.room_type if listing else "—"),
        ("Rental reference", f"Occupancy #{sublet_request.current_occupancy_id}"),
    ]
    agreement = occupancy.offer.agreement if occupancy and occupancy.offer else None
    if agreement is not None:
        property_rows.append(("Occupancy agreement", f"Agreement #{agreement.id}"))
    if occupancy is not None:
        property_rows.append(("Tenancy end date", day(occupancy.expected_end_date)))

    decider_party = decider.party_id if sublet_request.decided_by_user_id and decider else None
    party_rows = [
        ("Requesting tenant", requester.name if requester else "—", "Asked the host for permission",
         verified(_guest_identity_verified(db, requester))),
        ("Co-tenant" if is_shared_room else "Proposed occupant", proposed_name,
         _OCCUPANT_ROLE.get(arrangement, "Named in the request"),
         verified(_party_identity_verified(db, sublet_request.proposed_renter_party_id))),
    ]
    if sublet_request.decided_by_user_id or sublet_request.decided_by_admin_id:
        party_rows.append(("Decision maker", authority, "Decided the request",
                           verified(_party_identity_verified(db, decider_party)) if decider_party else "—"))

    terms_rows = [("Arrangement", _ARRANGEMENT_DESCRIPTIONS.get(arrangement, arrangement))]
    if sublet_request.proposed_start_date or sublet_request.proposed_end_date:
        terms_rows.append(("Proposed period", f"{day(sublet_request.proposed_start_date)} to {day(sublet_request.proposed_end_date)}"))
    if snapshot.get("proposed_monthly_rent") is not None:
        label = "Agreed amount paid to the renter" if is_shared_room else "Proposed monthly rent"
        terms_rows.append((label, f"{currency} {float(snapshot['proposed_monthly_rent']):,.2f} / month".strip()))
    if sublet_request.reason:
        terms_rows.append(("Tenant's reason", sublet_request.reason))
    if sublet_request.status in ("approved", "ended"):
        terms_rows += [
            ("Original tenant's responsibility", _LIABILITY_LABELS.get(sublet_request.original_renter_liability, sublet_request.original_renter_liability)),
            ("New occupant's responsibility", _LIABILITY_LABELS.get(sublet_request.new_occupant_liability, sublet_request.new_occupant_liability)),
            ("Deposit", _DEPOSIT_LABELS.get(sublet_request.deposit_disposition, sublet_request.deposit_disposition or "—")),
            ("Who is paid", _payee_text(sublet_request)),
        ]
        if sublet_request.occupant_risk_tier and sublet_request.occupant_risk_tier != "NONE":
            terms_rows.append(("Overlap check", f"{sublet_request.occupant_risk_tier}: {sublet_request.occupant_risk_reason}"))

    conditions = [c for c in (sublet_request.approval_condition_list or []) if c]
    if sublet_request.approval_conditions and sublet_request.approval_conditions not in conditions:
        conditions.append(sublet_request.approval_conditions)
    notes = sublet_request.admin_notes or ""
    if sublet_request.end_reason:
        notes = (notes + "\n" if notes else "") + f"Co-tenancy ended: {sublet_request.end_reason}"

    jurisdiction = snapshot.get("jurisdiction") or "—"
    model = SubletDecisionModel(
        record_ref=f"ZR-SUB-{sublet_request.id:06d}",
        status_label=_DECISION_LABELS.get(sublet_request.status, sublet_request.status.replace("_", " ").title()),
        arrangement_label=_ARRANGEMENT_SHORT.get(arrangement, arrangement),
        decided_on=day(sublet_request.decided_at or sublet_request.withdrawn_at or sublet_request.co_tenant_responded_at),
        jurisdiction_label=jurisdiction,
        summary_rows=summary_rows, property_rows=property_rows, party_rows=party_rows, terms_rows=terms_rows,
        conditions=conditions, decision_notes=notes,
        history_rows=[
            (when(e.timestamp), e.summary.replace(f"({arrangement})", f"({_ARRANGEMENT_SHORT.get(arrangement, arrangement).lower()})"))
            for e in build_sublet_audit_trail(sublet_request)
        ],
    )
    model.certificate_rows = [
        ("Record ID", model.record_ref),
        ("Request version", str(sublet_request.version)),
        ("Market policy", f"{jurisdiction}, pack v{snapshot.get('policy_pack_version', '—')}"),
        ("Generated", generated_at_label()),
        ("Content fingerprint (SHA-256)", content_fingerprint(model)),
    ]
    return render_sublet_decision_pdf(model)


_DECISION_LABELS = {
    "approved": "Approved", "rejected": "Declined", "withdrawn": "Withdrawn by tenant", "expired": "Approval expired",
    "superseded": "Superseded", "cancelled_by_authority": "Cancelled by authority",
    "declined_by_co_tenant": "Declined by co-tenant", "ended": "Approved (co-tenancy since ended)",
}
_ARRANGEMENT_SHORT = {
    "ASSIGNMENT_FULL": "Full handover", "REPLACEMENT_OCCUPANT": "Replacement occupant",
    "SUBLEASE_PARTIAL": "Partial sublease", "ADD_CO_TENANT": "Co-tenant", "LODGER_OR_LICENSEE": "Lodger / licensee",
    "ADDITIONAL_OCCUPANT": "Additional occupant",
}
_ARRANGEMENT_DESCRIPTIONS = {
    "ASSIGNMENT_FULL": "Full handover — the tenant moves out and the new occupant takes over the tenancy completely.",
    "REPLACEMENT_OCCUPANT": "Replacement occupant — the tenant moves out and the new occupant lives in the room.",
    "SUBLEASE_PARTIAL": "Partial sublease — the tenant stays on and the subtenant gets their own agreement.",
    "ADD_CO_TENANT": "Co-tenant — shares the room with the tenant and pays the tenant directly; the tenant stays responsible to the host.",
    "LODGER_OR_LICENSEE": "Lodger / licensee — lives in the room without full tenancy rights.",
    "ADDITIONAL_OCCUPANT": "Additional occupant — permitted to live in the room with no tenancy or agreement of their own.",
}
_OCCUPANT_ROLE = {
    "ASSIGNMENT_FULL": "Takes over the tenancy", "REPLACEMENT_OCCUPANT": "Replaces the tenant as occupant",
    "SUBLEASE_PARTIAL": "Subtenant with their own agreement", "ADD_CO_TENANT": "Shares the room with the tenant",
    "LODGER_OR_LICENSEE": "Lodger / licensee", "ADDITIONAL_OCCUPANT": "Permitted to reside",
}
_LIABILITY_LABELS = {
    "ACTIVE": "Remains fully responsible", "LIMITED": "Remains responsible on a limited basis", "RELEASED": "Released",
    "NONE": "No responsibility to the host", "SUBORDINATE": "Responsible under the tenant's tenancy",
    "ASSIGNEE": "Takes over full responsibility", "JOINT": "Jointly responsible", "LICENSEE": "Licensee only",
}
_DEPOSIT_LABELS = {
    "NOT_APPLICABLE": "Not affected", "SEPARATE_DEPOSIT_CREATED": "A separate deposit applies to the new occupant", "NO_SEPARATE_DEPOSIT": "No separate deposit for the new occupant",
    "RETAINED_BY_ORIGINAL_TENANCY": "Stays with the original tenancy",
}


def _payee_text(sublet_request: SubletRequest) -> str:
    if sublet_request.arrangement_type in SHARED_ROOM_ARRANGEMENT_TYPES:
        return "The co-tenant pays the tenant directly; the tenant keeps paying the host."
    return {
        "ORIGINAL_RENTER_PAYEE": "The new occupant pays the original tenant.",
        "HOST_OR_LANDLORD_PAYEE": "The new occupant pays the host directly.",
        "EXTERNAL_PAYEE_RECORDED": "No payment is made through this arrangement.",
    }.get(sublet_request.payee_model, sublet_request.payee_model or "—")


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
    """Read-only oversight list of sublet requests awaiting the host's
    decision -- the decision itself is the host's alone."""
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only super admins can view all sublet requests")

    return list(
        db.scalars(
            select(SubletRequest)
            .options(
                joinedload(SubletRequest.current_occupancy).joinedload(Occupancy.listing),
                joinedload(SubletRequest.current_occupancy).joinedload(Occupancy.guest),
            )
            .where(SubletRequest.status.in_(DECIDABLE_SUBLET_REQUEST_STATUSES))
            .order_by(SubletRequest.created_at.desc())
        )
    )


def sweep_expired_sublet_approvals(db: Session) -> list[SubletRequest]:
    """ZR-SUB-003 Section 6 EXPIRED: 'Configured decision/request period
    expired | System.' The only real expiry timestamp this MVP has is an
    APPROVED request's own approval_expires_at (Section 5.2) -- no scheduler
    exists anywhere in this stack (see services/evidence_retention.py's own
    admission of the same gap) to enforce it automatically, so this is a
    manual/admin-triggered sweep, same shape as every other sweep here.
    Marking EXPIRED is a record-only status flag -- like approval_expires_at
    itself, it does not reverse whatever the approval already did."""
    now = datetime.now(timezone.utc)
    candidates = list(
        db.scalars(
            select(SubletRequest).where(
                SubletRequest.status == "approved",
                SubletRequest.approval_expires_at.is_not(None),
                SubletRequest.approval_expires_at <= now,
            )
        )
    )
    for sr in candidates:
        sr.status = "expired"
        sr.expired_at = now
        # ZR-SUB-003 Section 9 notification matrix has no explicit EXPIRED
        # row, but "Request approaching configured deadline" establishes the
        # same expectation -- both sides should hear about it, not just
        # discover it later. A dedicated notification_type, not a reuse of
        # _notify_sublet_requester/_notify_sublet_host's approved/rejected
        # verbs, which would otherwise mislabel this as a rejection.
        if sr.requested_by_guest_id:
            guest = db.get(Guest, sr.requested_by_guest_id)
            if guest:
                notif_crud.notify_user_by_guest(
                    db, guest,
                    title="Your sublet approval has expired",
                    message="The approval window for your sublet arrangement has expired.",
                    notification_type="sublet_request.expired",
                    related_entity_type="sublet_request", related_entity_id=str(sr.id),
                )
        listing = sr.current_occupancy.listing if sr.current_occupancy else None
        if listing and listing.party_id:
            notif_crud.notify_user_by_party(
                db, listing.party_id,
                title="A sublet approval has expired",
                message=f'The approved sublet arrangement for "{listing.name}" has expired.',
                notification_type="sublet_request.expired",
                related_entity_type="sublet_request", related_entity_id=str(sr.id),
            )
    db.commit()
    return candidates


def supersede_sublet_request(
    db: Session, admin: AdminUser, old_request: SubletRequest, new_request: SubletRequest,
) -> SubletRequest:
    """ZR-SUB-003 Section 6 SUPERSEDED: 'A newer accepted request replaces
    the prior record | System/authorized workflow.' No automatic inference
    exists for this (no sublet_request_snapshot/revision model to detect "a
    newer accepted request" from) -- this is the real, admin-invoked
    workflow the doc calls for. Both requests must already be approved for
    the same occupancy; the old record is preserved (never deleted), just
    flagged and linked."""
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a Super Admin can mark a sublet request as superseded")
    if old_request.status != "approved" or new_request.status != "approved":
        raise HTTPException(status.HTTP_409_CONFLICT, "Both requests must already be approved to record a supersession")
    if old_request.current_occupancy_id != new_request.current_occupancy_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Both requests must belong to the same occupancy")
    if old_request.id == new_request.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A request cannot supersede itself")

    old_request.status = "superseded"
    old_request.superseded_by_sublet_request_id = new_request.id

    # ZR-SUB-003 Section 9: "Decision/conditions superseded | Material-change
    # notice" -- both sides.
    if old_request.requested_by_guest_id:
        guest = db.get(Guest, old_request.requested_by_guest_id)
        if guest:
            notif_crud.notify_user_by_guest(
                db, guest,
                title="Your sublet arrangement was superseded",
                message=f"Sublet request #{old_request.id} has been replaced by a newer approved request (#{new_request.id}).",
                notification_type="sublet_request.superseded",
                related_entity_type="sublet_request", related_entity_id=str(old_request.id),
            )
    listing = old_request.current_occupancy.listing if old_request.current_occupancy else None
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="A sublet decision was superseded",
            message=f'Sublet request #{old_request.id} for "{listing.name}" has been replaced by request #{new_request.id}.',
            notification_type="sublet_request.superseded",
            related_entity_type="sublet_request", related_entity_id=str(old_request.id),
        )

    db.commit()
    db.refresh(old_request)
    return old_request


def cancel_sublet_decision_by_authority(
    db: Session, admin: AdminUser, sublet_request: SubletRequest, reason: str,
) -> SubletRequest:
    """ZR-SUB-003 Section 6 CANCELLED_BY_AUTHORITY: 'Decision record changed
    through an authorized, legally valid process; original remains preserved
    | Restricted workflow.' Super-Admin-only, mandatory reason -- same AC-29
    role+reason discipline as crud/termination.py:set_tribunal_liability.
    This flags an already-decided request as legally cancelled/rescinded for
    the record; it does NOT automatically reverse whatever the original
    decision already executed (an occupancy reassignment, a co-tenant's new
    agreement, etc.) -- any real-world reversal is a separate, manual,
    case-by-case admin operation."""
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a Super Admin can cancel a sublet decision by authority")
    if sublet_request.status not in ("approved", "rejected"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an already-decided sublet request can be cancelled by authority")
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A reason is required to cancel a decision by authority")

    sublet_request.status = "cancelled_by_authority"
    sublet_request.cancelled_by_authority_at = datetime.now(timezone.utc)
    sublet_request.cancelled_by_authority_admin_id = admin.id
    sublet_request.cancelled_by_authority_reason = reason.strip()

    # ZR-SUB-003 Section 9: same "material-change notice" expectation as a
    # supersession -- both sides need to know their decision was overridden.
    if sublet_request.requested_by_guest_id:
        guest = db.get(Guest, sublet_request.requested_by_guest_id)
        if guest:
            notif_crud.notify_user_by_guest(
                db, guest,
                title="Your sublet decision was cancelled by authority",
                message=f"Sublet request #{sublet_request.id}'s decision was cancelled: {reason.strip()}",
                notification_type="sublet_request.cancelled_by_authority",
                related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
            )
    listing = sublet_request.current_occupancy.listing if sublet_request.current_occupancy else None
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="A sublet decision was cancelled by authority",
            message=f'Sublet request #{sublet_request.id} for "{listing.name}" was cancelled: {reason.strip()}',
            notification_type="sublet_request.cancelled_by_authority",
            related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
        )

    db.commit()
    db.refresh(sublet_request)
    return sublet_request


def build_sublet_audit_trail(sublet_request: SubletRequest) -> list[SubletChronologyEvent]:
    """ZR-SUB-003 Section 12: 'GET /{id}/audit: Privileged audit view; not
    ordinary user endpoint.' Reconstructed from this request's own
    timestamp fields, same approach as services/dispute_case_export.py's
    _chronology_events -- a SubletRequest is a single row with every
    relevant timestamp already on it, so there's no separate event table to
    query."""
    events: list[SubletChronologyEvent] = []

    def add(timestamp: datetime | None, event_type: str, summary: str) -> None:
        if timestamp is not None:
            events.append(SubletChronologyEvent(timestamp=timestamp, event_type=event_type, summary=summary))

    add(sublet_request.created_at, "sublet_request.submitted", f"Sublet request #{sublet_request.id} submitted ({sublet_request.arrangement_type}).")
    if sublet_request.co_tenant_responded_at is not None:
        accepted = sublet_request.status != "declined_by_co_tenant"
        add(sublet_request.co_tenant_responded_at, "sublet_request.co_tenant_" + ("accepted" if accepted else "declined"),
            "Co-tenant accepted the request." if accepted else "Co-tenant declined the request.")
    add(sublet_request.info_requested_at, "sublet_request.more_information_requested", f"More information requested: {sublet_request.info_request_note[:120]}")
    add(sublet_request.info_responded_at, "sublet_request.tenant_response_submitted", f"Tenant responded: {sublet_request.info_response_note[:120]}")
    if sublet_request.decided_at is not None:
        add(sublet_request.decided_at, f"sublet_request.{sublet_request.admin_decision}", f"Decision recorded: {sublet_request.admin_decision}.")
    add(sublet_request.withdrawn_at, "sublet_request.withdrawn", "Request withdrawn by the tenant.")
    add(sublet_request.ended_at, "sublet_request.co_tenancy_ended", "Co-tenancy ended.")
    add(sublet_request.expired_at, "sublet_request.expired", "Approval expired.")
    add(sublet_request.cancelled_by_authority_at, "sublet_request.cancelled_by_authority", f"Decision cancelled by authority: {sublet_request.cancelled_by_authority_reason[:120]}")
    if sublet_request.superseded_by_sublet_request_id is not None:
        add(sublet_request.decided_at, "sublet_request.superseded", f"Superseded by sublet request #{sublet_request.superseded_by_sublet_request_id}.")

    events.sort(key=lambda event: event.timestamp)
    return events


# -- shared room (ADD_CO_TENANT) ---------------------------------------------------

def _shared_room_or_404(sublet_request: SubletRequest | None) -> SubletRequest:
    if sublet_request is None or sublet_request.arrangement_type not in SHARED_ROOM_ARRANGEMENT_TYPES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Co-tenancy not found")
    return sublet_request


def list_shared_room_requests_for_party(db: Session, party_id: int) -> list[SubletRequest]:
    """Every co-tenancy request that names this person -- the co-tenant's own
    view (the renter's list only holds requests they submitted)."""
    return list(
        db.scalars(
            select(SubletRequest)
            .where(
                SubletRequest.proposed_renter_party_id == party_id,
                SubletRequest.arrangement_type.in_(SHARED_ROOM_ARRANGEMENT_TYPES),
            )
            .order_by(SubletRequest.created_at.desc())
        )
    )


def get_shared_room_for_co_tenant_or_404(db: Session, sublet_request_id: int, user: UserAccount) -> SubletRequest:
    sublet_request = _shared_room_or_404(db.get(SubletRequest, sublet_request_id))
    if not user.party_id or sublet_request.proposed_renter_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This co-tenancy doesn't name you")
    return sublet_request


def end_shared_room(db: Session, sublet_request: SubletRequest, user: UserAccount, reason: str = "") -> SubletRequest:
    """The renter, the co-tenant or the Host ends an active shared room; the
    other two are told."""
    _shared_room_or_404(sublet_request)
    if not is_active_shared_room(sublet_request):
        raise HTTPException(status.HTTP_409_CONFLICT, "This room isn't currently shared")
    occupancy = sublet_request.current_occupancy
    listing = occupancy.listing
    guest = guest_crud.get_guest_for_user(db, user)
    is_renter = guest is not None and guest.id == occupancy.guest_id
    is_co_tenant = bool(user.party_id) and user.party_id == sublet_request.proposed_renter_party_id
    is_host = bool(user.party_id) and listing is not None and party_id_for_listing(listing) == user.party_id
    if not (is_renter or is_co_tenant or is_host):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the renter, the co-tenant or the host can end this co-tenancy")

    sublet_request.status = "ended"
    sublet_request.ended_at = datetime.now(timezone.utc)
    sublet_request.ended_by_user_id = user.id
    sublet_request.end_reason = reason.strip()[:2000]
    # Rent that hasn't started being paid and falls after the end is no longer owed.
    from app.services.shared_room_payments import cancel_unpaid_rent_after

    cancel_unpaid_rent_after(db, sublet_request, on_or_after=sublet_request.ended_at.date())

    room_name = f'"{listing.name}"' if listing else "the room"
    message = f"{user.full_name} ended the co-tenancy of {room_name}." + (f" Reason: {reason.strip()}" if reason.strip() else "")
    common = dict(
        title="Co-tenancy ended", message=message, notification_type="sublet_request.co_tenancy_ended",
        related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
    )
    if not is_renter and occupancy.guest is not None:
        notif_crud.notify_user_by_guest(db, occupancy.guest, **common)
    if not is_co_tenant and sublet_request.proposed_renter_party_id:
        notif_crud.notify_user_by_party(db, sublet_request.proposed_renter_party_id, **common)
    if not is_host and listing is not None and listing.party_id:
        notif_crud.notify_user_by_party(db, listing.party_id, **common)
    db.commit()
    db.refresh(sublet_request)
    return sublet_request
