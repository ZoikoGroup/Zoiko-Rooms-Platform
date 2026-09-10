from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.mailer import send_listing_published_email, send_listing_rejected_email
from app.crud import notification as notification_crud
from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.crud.ids import new_id, slugify
from app.crud.identity_verification import get_verified_identity_for_party
from app.crud.user import get_user_by_party_id
from app.models.admin_user import AdminUser
from app.models.leasing import Agreement, Offer
from app.models.listing import LISTING_STATES, Listing, MAX_LISTING_IMAGES, SUPPORTED_CURRENCIES
from app.models.listing_approval import ListingApproval
from app.models.listing_version import ListingVersion
from app.models.market_release import MarketRelease
from app.models.occupancy import Occupancy
from app.models.room import Room
from app.models.room_hold import RoomHold
from app.schemas.listing import ListingCreate, ListingUpdate, PublicListingRead
from app.services.eligibility import jurisdiction_gates_pass, listing_publication_eligible
from app.services.listing_versioning import build_snapshot, classify_material_change, compute_content_hash
from app.services.policy import get_policy


def _occupied_room_ids(db: Session) -> set[int]:
    """Rooms committed to a tenant right now. Single source of truth for "is
    this room actually available" -- every place that decides whether a
    listing should be shown as live/bookable must go through this (or
    annotate_availability/list_public_listings below) rather than trusting
    Listing.state alone, which only reflects the admin approval workflow and
    says nothing about whether a renter has since signed a lease or moved in.

    Excludes only ENDED occupancies: PENDING_MOVE_IN already means a signed
    agreement committed this room to a specific renter (Occupancy is only
    created once the agreement is signed -- see models/occupancy.py), so a
    room isn't "free again" just because move-in hasn't physically happened
    yet.

    ZR-ENG-CLR-001 Section 1, Rule 4: also excludes any room under an active
    Inventory Service hold (app/services/inventory.py) -- a hold is created
    the moment an offer is *accepted*, well before Occupancy exists, and a
    second applicant must not be able to apply (or see the room as bookable)
    while that hold is active."""
    occupied = set(db.scalars(select(Occupancy.room_id).where(Occupancy.status != "ENDED")))
    held = set(db.scalars(select(RoomHold.room_id).where(RoomHold.released_at.is_(None))))
    return occupied | held


def _canonical_location(db: Session, room_id: int | None) -> dict:
    """Property.address/city is the canonical source of truth for where a
    listing physically is -- resolves the property/listing conflict where a
    listing's independently-typed city/location could otherwise drift from
    the property record it belongs to. Deliberately does not touch
    latitude/longitude: no geocoding provider is wired up, and integrating one
    is a separate, explicitly out-of-scope product decision."""
    if room_id is None:
        return {}
    room = db.get(Room, room_id)
    if room is None or room.property is None:
        return {}
    return {"city": room.property.city, "location": room.property.address}


def is_listing_available(db: Session, listing: Listing) -> bool:
    """True only when the listing is published, its room (if any) is active,
    and that room has no active occupancy. Read-only -- never mutates
    Listing.state, which stays admin-approval-workflow-only.

    Deliberately does NOT also re-check jurisdiction_gates_pass here (unlike
    services/eligibility.py's own failed_gate_visibility_allowed, which
    exists and is tested standalone): jurisdiction gates are checked at
    publish/agreement/move-in time (check_publish_eligibility,
    check_agreement_eligibility, check_move_in_eligibility) but are
    deliberately informational-only at publish time in this codebase ("the
    admin's decision to approve is the final authority, not an automated
    compliance gate" -- see publish_listing). Making a PUBLISHED listing's
    day-to-day visibility hinge on continuously-passing jurisdiction gates
    would be a real platform-wide behavior change (most listings in this
    codebase have no ongoing gate enforcement wired at all beyond the
    publish-time check), not a config-wiring fix -- out of Section 1's own
    guardrail against inventing new hard business rules."""
    if listing_publication_eligible(listing):
        return False
    if listing.room_id is None:
        return True
    room = listing.room
    if room is not None and room.status != "active":
        return False
    return listing.room_id not in _occupied_room_ids(db)


def annotate_availability(db: Session, listings: list[Listing]) -> list[Listing]:
    """Set a transient `.available` attribute (not a DB column) on each listing
    so ListingRead can expose real-time availability without duplicating this
    query's logic at every call site. Bulk-computes the occupied set once."""
    occupied = _occupied_room_ids(db)
    for listing in listings:
        room = listing.room
        listing.available = (
            listing.state == "PUBLISHED"
            and (listing.room_id is None or (room is not None and room.status == "active"))
            and listing.room_id not in occupied
        )
    return listings


def _validate_currency(currency: str) -> None:
    if currency not in SUPPORTED_CURRENCIES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported currency '{currency}'. Supported currencies: {', '.join(SUPPORTED_CURRENCIES)}",
        )


def _validate_image_count(images: list[str]) -> None:
    if len(images) > MAX_LISTING_IMAGES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"A listing can have at most {MAX_LISTING_IMAGES} images ({len(images)} given)",
        )


def _resolve_market_release_id_for_room(db: Session, room_id: int | None) -> int | None:
    """A listing's market is derived from its room's owning party's jurisdiction --
    never hand-picked by the provider, so a listing can't be steered toward a more
    permissive market than the one it actually operates in."""
    if room_id is None:
        return None
    room = db.get(Room, room_id)
    if room is None:
        return None
    jurisdiction = room.property.owner_party.jurisdiction
    release = db.scalar(select(MarketRelease).where(MarketRelease.jurisdiction == jurisdiction))
    return release.id if release else None


def _create_new_version(db: Session, listing: Listing) -> ListingVersion:
    """ZR-ENG-CLR-001 Rule 3: every content edit creates a new immutable
    ListingVersion, classified material vs non-material against whichever
    version was most recently reviewed (current_public_version, falling back
    to current_draft_version for a listing that's never been published yet).
    A non-material change on an already-published listing can replace the
    public snapshot immediately (no re-review); a material change only ever
    updates current_draft_version_id -- current_public_version_id is
    untouched until an explicit approval (see approve_listing)."""
    reference_version = listing.current_public_version or listing.current_draft_version
    previous_snapshot = reference_version.snapshot if reference_version else None
    current_snapshot = build_snapshot(listing)
    flags, is_material = classify_material_change(previous_snapshot, current_snapshot)

    last_version_no = db.scalar(
        select(func.max(ListingVersion.version_no)).where(ListingVersion.listing_id == listing.id)
    ) or 0

    version = ListingVersion(
        listing_id=listing.id,
        version_no=last_version_no + 1,
        snapshot=current_snapshot,
        content_hash=compute_content_hash(current_snapshot),
        material_change_flags=flags,
        is_material=is_material,
        approval_status="DRAFT",
    )
    db.add(version)
    db.flush()

    listing.current_draft_version_id = version.id
    if not is_material and listing.current_public_version_id is not None:
        # Non-material change on an already-published listing: promote
        # immediately, no review needed (spec 6.2). Recorded as its own
        # auto-approval decision, not silently skipped, so the approval
        # history stays a complete record of every promotion to public.
        version.approval_status = "APPROVED"
        version.approved_at = datetime.now(timezone.utc)
        db.add(ListingApproval(
            listing_version_id=version.id,
            decision="APPROVED",
            decision_reason_code="non_material_auto_promote",
            reviewer_authority_scope="system",
        ))
        listing.current_public_version_id = version.id
    db.flush()
    return version


def list_listings_for(db: Session, admin: AdminUser) -> list[Listing]:
    """Every admin (not just super_admin) sees every listing, including USER-hosted
    ones -- ADMIN's job includes reviewing listings submitted by any host, so
    visibility here is operational, not per-admin-owner scoped. Mutating someone
    else's listing content is still owner-or-super-admin gated separately
    (see _assert_owner_or_super_admin in api/routes/listings.py); this only
    affects what an admin can see and review/approve/reject."""
    return list(db.scalars(select(Listing).options(joinedload(Listing.room)).order_by(Listing.name)))


def list_public_listings(
    db: Session,
    *,
    city: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    room_type: str | None = None,
    amenities: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
    exclude_party_id: int | None = None,
) -> tuple[list[Listing], int]:
    """Server-side filtered, paginated public listing search. Only ever returns
    PUBLISHED listings -- unpublished/withdrawn/draft/etc. states are never
    reachable through this path regardless of what filters are supplied.
    Also excludes listings whose room is currently occupied (active occupancy)
    or inactive -- state=PUBLISHED alone doesn't mean a renter hasn't since
    moved in, and search results must never show an occupied room as bookable.

    exclude_party_id: when the caller is an authenticated USER, their own
    party's listings are left out of their own search results -- a host should
    never see (or be able to apply to) a room they list themselves. Admin-owned
    listings (party_id is NULL) are never affected by this."""
    occupied_room_ids = _occupied_room_ids(db)
    conditions = [Listing.state == "PUBLISHED"]
    if occupied_room_ids:
        conditions.append(
            (Listing.room_id.is_(None)) | (Listing.room_id.not_in(occupied_room_ids))
        )
    conditions.append(
        (Listing.room_id.is_(None))
        | Listing.room_id.in_(select(Room.id).where(Room.status == "active"))
    )
    if city:
        conditions.append(Listing.city.ilike(f"%{city.strip()}%"))
    if room_type:
        conditions.append(Listing.room_type.ilike(f"%{room_type.strip()}%"))
    if min_price is not None:
        conditions.append(Listing.price_per_night >= min_price)
    if max_price is not None:
        conditions.append(Listing.price_per_night <= max_price)
    if amenities:
        # Postgres array-containment (@>): the listing's amenities must be a
        # superset of every amenity requested.
        conditions.append(Listing.amenities.contains(amenities))
    if exclude_party_id is not None:
        conditions.append(or_(Listing.party_id.is_(None), Listing.party_id != exclude_party_id))

    total = db.scalar(select(func.count()).select_from(Listing).where(*conditions)) or 0

    listings = list(
        db.scalars(
            select(Listing)
            .options(joinedload(Listing.owner))
            .where(*conditions)
            .order_by(Listing.name)
            .limit(limit)
            .offset(offset)
        )
    )
    return listings, total


def get_listing(db: Session, listing_id: str) -> Listing | None:
    return db.get(Listing, listing_id)


def to_public_listing_read(listing: Listing) -> PublicListingRead:
    """ZR-ENG-CLR-001 Rule 3 (AC-03): public content is served from the
    immutable current_public_version snapshot, never the live Listing row --
    a pending draft edit (approved or not) can never change what's already
    public until it's explicitly approved and promoted (see approve_listing/
    _create_new_version). Falls back to the live row only for a listing with
    no version yet at all (shouldn't happen for anything actually PUBLISHED,
    but keeps this function safe to call regardless)."""
    snapshot = listing.current_public_version.snapshot if listing.current_public_version_id else None

    def field(name: str):
        if snapshot is not None and name in snapshot:
            return snapshot[name]
        return getattr(listing, name)

    return PublicListingRead(
        id=listing.id,
        slug=listing.slug,
        name=field("name"),
        property_type=field("property_type"),
        room_type=field("room_type"),
        city=field("city"),
        location=field("location"),
        latitude=field("latitude"),
        longitude=field("longitude"),
        price_per_night=field("price_per_night"),
        currency=field("currency"),
        rating=listing.rating,
        review_count=listing.review_count,
        guests=field("guests"),
        bedrooms=field("bedrooms"),
        bathrooms=field("bathrooms"),
        size=field("size"),
        images=field("images"),
        amenities=field("amenities"),
        tags=field("tags"),
        description=field("description"),
        featured=field("featured"),
        room_id=field("room_id"),
        min_stay_nights=field("min_stay_nights"),
        owner_name=listing.contact_name or (listing.owner.full_name if listing.owner else "Host"),
    )


def create_listing(db: Session, data: ListingCreate, owner: AdminUser) -> Listing:
    if data.min_stay_nights < 30:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Minimum stay must be at least 30 nights")
    _validate_currency(data.currency)
    _validate_image_count(data.images)

    payload = data.model_dump()
    payload.update(_canonical_location(db, data.room_id))
    listing = Listing(
        id=new_id("L"),
        slug=slugify(data.name),
        rating=4.5,
        review_count=0,
        owner_id=owner.id,
        state="DRAFT",
        market_release_id=_resolve_market_release_id_for_room(db, data.room_id),
        **payload,
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)
    _create_new_version(db, listing)
    db.commit()
    db.refresh(listing)
    return listing


def create_listing_for_party(db: Session, data: ListingCreate, party_id: int) -> Listing:
    """Create a USER-hosted draft listing without requiring an AdminUser."""
    if data.min_stay_nights < 30:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Minimum stay must be at least 30 nights")
    if data.room_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A hosted listing must be linked to one of your rooms")
    _validate_currency(data.currency)
    _validate_image_count(data.images)

    assert_party_owns_room(db, data.room_id, party_id)
    payload = data.model_dump()
    payload.update(_canonical_location(db, data.room_id))
    listing = Listing(
        id=new_id("L"), slug=slugify(data.name), rating=4.5, review_count=0,
        owner_id=None, party_id=party_id, state="DRAFT",
        market_release_id=_resolve_market_release_id_for_room(db, data.room_id),
        **payload,
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)
    _create_new_version(db, listing)
    db.commit()
    db.refresh(listing)
    return listing


def assert_party_owns_listing(listing: Listing, party_id: int) -> None:
    if listing.party_id != party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only manage listings owned by your party")


def assert_party_does_not_own_listing(listing: Listing, party_id: int) -> None:
    """The inverse of assert_party_owns_listing -- a renter can apply to any
    listing except one their own party hosts."""
    if listing.party_id is not None and listing.party_id == party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot apply to your own listing")


def assert_party_owns_room(db: Session, room_id: int, party_id: int) -> None:
    room = db.get(Room, room_id)
    if not room or room.property.owner_party_id != party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only list rooms owned by your party")


def update_listing(db: Session, listing: Listing, data: ListingUpdate) -> Listing:
    if data.min_stay_nights is not None and data.min_stay_nights < 30:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Minimum stay must be at least 30 nights")
    if data.currency is not None:
        _validate_currency(data.currency)
    if data.images is not None:
        _validate_image_count(data.images)

    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(listing, field, value)
    if "room_id" in updates:
        listing.market_release_id = _resolve_market_release_id_for_room(db, listing.room_id)
        for field, value in _canonical_location(db, listing.room_id).items():
            setattr(listing, field, value)
    db.commit()
    db.refresh(listing)
    _create_new_version(db, listing)
    db.commit()
    db.refresh(listing)
    return listing


def delete_listing(db: Session, listing: Listing) -> None:
    db.delete(listing)
    db.commit()


def duplicate_listing(db: Session, listing: Listing, owner: AdminUser) -> Listing:
    copy = Listing(
        id=new_id("L"),
        slug=f"{listing.slug}-copy-{new_id('').lower().lstrip('-')}",
        name=f"{listing.name} (Copy)",
        property_type=listing.property_type,
        room_type=listing.room_type,
        city=listing.city,
        location=listing.location,
        latitude=listing.latitude,
        longitude=listing.longitude,
        price_per_night=listing.price_per_night,
        currency=listing.currency,
        rating=listing.rating,
        review_count=0,
        guests=listing.guests,
        bedrooms=listing.bedrooms,
        bathrooms=listing.bathrooms,
        size=listing.size,
        images=list(listing.images),
        amenities=list(listing.amenities),
        tags=list(listing.tags),
        description=listing.description,
        featured=False,
        room_id=listing.room_id,
        min_stay_nights=listing.min_stay_nights,
        market_release_id=listing.market_release_id,
        contact_name=listing.contact_name,
        contact_phone=listing.contact_phone,
        contact_email=listing.contact_email,
        owner_id=owner.id,
        state="DRAFT",
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)
    return copy


def _guard_listing_transition(listing: Listing, action: str, allowed_from: tuple[str, ...]) -> None:
    if listing.state not in allowed_from:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A listing in state {listing.state} cannot be {action} (must currently be one of: {', '.join(allowed_from)})",
        )


def _has_unresolved_commitments(db: Session, room_id: int) -> bool:
    """ZR-ENG-CLR-001 Section 8: archive is 'not while unresolved commitments
    exist'. A commitment is live if some other Listing pointing at this same
    room_id has an accepted-but-not-yet-superseded Offer, or an Agreement that
    hasn't reached a terminal state -- both represent an obligation the
    platform must still honor regardless of what happens to this Listing row."""
    active_offer = db.scalar(
        select(Offer.id)
        .join(Listing, Listing.id == Offer.listing_id)
        .where(Listing.room_id == room_id, Offer.status == "ACCEPTED")
    )
    if active_offer is not None:
        return True
    active_agreement = db.scalar(
        select(Agreement.id)
        .join(Offer, Offer.id == Agreement.offer_id)
        .join(Listing, Listing.id == Offer.listing_id)
        .where(
            Listing.room_id == room_id,
            Agreement.status.in_(("SENT", "PARTIALLY_EXECUTED", "PAYMENT_IN_PROGRESS", "PAYMENT_PENDING", "SIGNED")),
        )
    )
    return active_agreement is not None


def pause_listing(db: Session, listing: Listing) -> Listing:
    """ZR-ENG-CLR-001 Rule 5/Section 8: 'Pause listing -- Allowed... Stops new
    booking eligibility; future/active bookings remain accessible and valid.'
    Only legal from PUBLISHED -- there is no new demand to stop otherwise.
    Never touches current_public_version_id or any Application/Offer/Agreement
    row (AC-06: pausing must never cascade into cancelling a confirmed
    booking) -- this function does nothing but flip Listing.state/paused_at."""
    _guard_listing_transition(listing, "paused", ("PUBLISHED",))
    listing.state = "PAUSED"
    listing.paused_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(listing)
    return listing


def resume_listing(db: Session, listing: Listing) -> Listing:
    """Section 12.2 ResumeListing: PAUSED -> PUBLISHED, resuming new booking
    eligibility for an already-approved, already-published listing. Distinct
    from publish_listing (which also handles the first-time DRAFT/APPROVED
    publish paths and can imply an approval decision) -- resume never touches
    approval state, since a paused listing was already published once and its
    current_public_version_id is already valid. publish_listing itself still
    also accepts PAUSED for backward compatibility -- this is the spec's own
    named command for the same transition, kept as a thinner, more specific
    action."""
    _guard_listing_transition(listing, "resumed", ("PAUSED",))
    listing.state = "PUBLISHED"
    listing.paused_at = None
    db.commit()
    db.refresh(listing)
    return listing


def withdraw_listing(db: Session, listing: Listing) -> Listing:
    """Rule 5/Section 8: 'Unpublish voluntarily -- Conditional... Removes new-
    marketplace visibility but cannot cancel confirmed commitments.' Same
    non-cascading guarantee as pause_listing (AC-06). QUARANTINED is included
    -- withdrawing is how a host/admin takes a quarantined listing out of
    consideration instead of waiting for it to be resolved back to PUBLISHED."""
    _guard_listing_transition(listing, "withdrawn", ("PUBLISHED", "PAUSED", "APPROVED", "QUARANTINED"))
    listing.state = "WITHDRAWN"
    db.commit()
    db.refresh(listing)
    return listing


def suspend_listing(db: Session, listing: Listing, reason: str) -> Listing:
    """Rule 5/Section 3: 'Admin/Super Admin suspension -- Allowed... May stop
    new bookings immediately.' An enforcement action, so (unlike pause/
    withdraw) it is legal from almost any non-terminal state; a reason is
    mandatory (Section 3: 'Suspend/quarantine -- Reason required')."""
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A suspension reason is required")
    _guard_listing_transition(listing, "suspended", tuple(s for s in LISTING_STATES if s not in ("ARCHIVED", "SUSPENDED")))
    listing.state = "SUSPENDED"
    listing.suspension_reason = reason.strip()
    db.commit()
    db.refresh(listing)
    return listing


def quarantine_listing(db: Session, listing: Listing, reason: str) -> Listing:
    """Rule 3/Section 6.2: 'If a new disclosure makes the currently published
    version unsafe or non-compliant, system/Admin may immediately quarantine
    or suspend the listing pending review.' Distinct from suspend_listing --
    quarantine specifically means 'this listing's own content/disclosures are
    now suspect, pending re-review', not a general enforcement action; only
    legal from PUBLISHED or PAUSED (there must be live public content for a
    disclosure to have made unsafe). A reason is mandatory, same as suspend."""
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A quarantine reason is required")
    _guard_listing_transition(listing, "quarantined", ("PUBLISHED", "PAUSED"))
    listing.state = "QUARANTINED"
    listing.suspension_reason = reason.strip()
    db.commit()
    db.refresh(listing)
    return listing


def archive_listing(db: Session, listing: Listing) -> Listing:
    """Rule 5/Section 8: 'Archive listing -- Not while unresolved commitments
    exist... Permitted only after bookings, disputes, refunds, and retention
    obligations reach allowed terminal states.' Only legal from WITHDRAWN or
    SUSPENDED, and only once _has_unresolved_commitments is false."""
    _guard_listing_transition(listing, "archived", ("WITHDRAWN", "SUSPENDED"))
    if listing.room_id is not None and _has_unresolved_commitments(db, listing.room_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This listing's room has an active offer or agreement and cannot be archived yet",
        )
    listing.state = "ARCHIVED"
    db.commit()
    db.refresh(listing)
    return listing


def check_publish_eligibility(db: Session, listing: Listing) -> list[str]:
    """Informational compliance signals for the admin review screen -- NOT a hard
    publish gate (see publish_listing). Returns a list of human-readable warnings;
    empty means every signal looks good. The one exception is "not linked to a
    room", which is a genuine structural requirement (there is nothing to publish)
    and is still enforced separately in publish_listing/submit_listing_for_review."""
    reasons: list[str] = []

    if listing.room_id is None:
        reasons.append("Listing is not linked to a room")
        return reasons

    if listing.min_stay_nights < 30:
        reasons.append("Minimum stay must be at least 30 nights")

    market_release = db.get(MarketRelease, listing.market_release_id) if listing.market_release_id else None
    if market_release and market_release.status == "active" and listing.min_stay_nights < market_release.min_stay_nights:
        reasons.append(f"Minimum stay must be at least {market_release.min_stay_nights} nights for this market")

    reasons.extend(jurisdiction_gates_pass(db, listing.room, market_release))

    provider_party_id = listing.room.property.owner_party_id
    identity = get_verified_identity_for_party(db, provider_party_id)
    if not identity:
        reasons.append("Provider identity verification is not approved")

    return reasons


def submit_listing_for_review(db: Session, listing: Listing) -> Listing:
    """USER-facing: DRAFT or REJECTED -> REVIEW. Publishing itself is always an
    explicit admin/super-admin decision from here on -- a USER can only ask for
    review, never publish directly."""
    if listing.room_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Listing must be linked to a room before it can be submitted")
    if listing.state not in ("DRAFT", "REJECTED", "CHANGES_REQUESTED"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"A listing in state {listing.state} cannot be submitted for review")

    listing.rejection_reason = ""
    listing.state = "REVIEW"
    db.commit()
    db.refresh(listing)

    # Safety net only for a listing with no version at all yet (created before
    # versioning existed) -- everything else (a version already sitting in
    # DRAFT from update_listing, or already auto-promoted to APPROVED because
    # the last edit was non-material) is left as-is; submit_listing_for_review
    # moves the *listing's* operational state, it doesn't itself invent content
    # to review.
    if listing.current_draft_version is None:
        _create_new_version(db, listing)
    if listing.current_draft_version.approval_status == "DRAFT":
        listing.current_draft_version.approval_status = "UNDER_REVIEW"
        listing.current_draft_version.submitted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(listing)

    # ZR-ENG-CLR-001 Rule 3/Section 14 policy key publication.requires_approval:
    # "Future low-risk automation may approve through the same auditable
    # approval object." England's own MarketRelease never overrides this
    # (the platform default is always True), so this branch is dead for
    # England launch -- it only fires for a future market pack that
    # explicitly sets the override to False.
    if not get_policy(listing.market_release, "publication.requires_approval"):
        return _auto_approve_and_publish_low_risk_market(db, listing)

    notification_crud.notify_all_admins(
        db,
        title="Listing pending review",
        message=f'"{listing.name}" was submitted and needs review.',
        notification_type="listing.submitted",
        related_entity_type="listing", related_entity_id=listing.id,
    )
    db.commit()
    return listing


def _auto_approve_and_publish_low_risk_market(db: Session, listing: Listing) -> Listing:
    """Section 14: the publication.requires_approval=False path. Goes through
    the exact same ListingApproval / current_public_version_id / PUBLISHED
    transition a human admin decision would (see approve_listing/
    publish_listing) -- just system-attributed (reviewer_authority_scope=
    'system', actor=None) instead of admin-attributed, and both decisions
    still get their own distinct audit + domain events (5.1: 'Approval and
    publication must be distinct events even if executed milliseconds
    apart'), same as publish_listing's own implicit-approval case."""
    version = listing.current_draft_version
    version.approval_status = "APPROVED"
    version.approved_at = datetime.now(timezone.utc)
    db.add(ListingApproval(
        listing_version_id=version.id,
        decision="APPROVED",
        decision_reason_code="publication_requires_approval_false",
        reviewer_authority_scope="system",
    ))
    listing.current_public_version_id = version.id
    listing.state = "PUBLISHED"
    if listing.published_at is None:
        listing.published_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(listing)

    log_audit_event(
        db, None, "listing.approve", "listing", listing.id, reason="publication_requires_approval_false",
        before_state="REVIEW", after_state="APPROVED", object_version=str(listing.current_public_version_id),
    )
    emit_event(
        db, "listing.approved", "listing", listing.id, {"listing_version_id": listing.current_public_version_id},
    )
    log_audit_event(
        db, None, "listing.publish", "listing", listing.id, reason="publication_requires_approval_false",
        before_state="APPROVED", after_state="PUBLISHED", object_version=str(listing.current_public_version_id),
    )
    emit_event(db, "listing.published", "listing", listing.id, {"room_id": listing.room_id})

    user = get_user_by_party_id(db, listing.party_id)
    if user:
        notification_crud.notify_user(
            db, user.id,
            title="Listing approved and published",
            message=f'Your listing "{listing.name}" has been automatically approved and published for this market.',
            notification_type="listing.published",
            related_entity_type="listing", related_entity_id=listing.id,
        )
        db.commit()
        send_listing_published_email(user.email, user.full_name, listing.name)
    return listing


def _record_approval_decision(
    db: Session, version: ListingVersion, decision: str, admin: AdminUser, reason_note: str = "",
    decision_reason_code: str | None = None,
) -> ListingApproval:
    """ZR-ENG-CLR-001 Section 1, Rule 2: every APPROVED/REJECTED transition of a
    ListingVersion is backed by a structured, attributable ListingApproval
    record -- never a bare status flip. `version.approval_status` is the
    current-state projection; the ListingApproval rows are the append-only
    decision log behind it."""
    approval = ListingApproval(
        listing_version_id=version.id,
        decision=decision,
        decision_reason_code=decision_reason_code,
        reason_note=reason_note,
        reviewer_admin_id=admin.id,
        reviewer_authority_scope=admin.role,
    )
    db.add(approval)
    return approval


def approve_listing(db: Session, listing: Listing, admin: AdminUser) -> Listing:
    """Admin/super-admin only (enforced at the route level). REVIEW -> APPROVED.
    The approval decision is recorded independently of publish_listing -- an
    APPROVED listing that later gets paused stays approved, so re-publishing it
    never has to re-run (or re-pass) any compliance check. No notification is
    sent here; the USER-facing "approved and published" notification fires once,
    from publish_listing, which is how the review UI's combined "Approve &
    Publish" action actually reaches the user (see PropertiesManager.tsx).

    ZR-ENG-CLR-001 Rule 2/3: this is also where the reviewed ListingVersion
    itself is approved and promoted to current_public_version_id -- approving
    the listing without also approving/promoting its pending version would
    leave "APPROVED" state pointing at stale or no public content."""
    if listing.state != "REVIEW":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a listing pending review can be approved")
    listing.state = "APPROVED"

    version = listing.current_draft_version
    if version is not None and version.approval_status != "APPROVED":
        version.approval_status = "APPROVED"
        version.approved_at = datetime.now(timezone.utc)
        _record_approval_decision(db, version, "APPROVED", admin)
        listing.current_public_version_id = version.id

    db.commit()
    db.refresh(listing)
    return listing


def publish_listing(db: Session, listing: Listing, admin: AdminUser) -> Listing:
    """Admin/super-admin only (enforced at the route level). check_publish_eligibility
    is informational -- it is deliberately NOT consulted here; the admin's decision
    to approve is the final authority, not an automated compliance gate. Works from
    any non-published state that has a room (DRAFT for an admin's own quick-publish,
    APPROVED for the normal review flow, PAUSED to resume a previously-approved
    listing) -- none of these re-check authority/occupancy/identity.

    ZR-ENG-CLR-001 AC-01: a listing MUST NOT become PUBLISHED without an
    APPROVED, immutable current_public_version behind it. For the normal
    review flow that's already true (approve_listing already promoted it);
    this is what makes it true for the DRAFT quick-publish shortcut too --
    the admin's publish action here IS the approval decision in that case."""
    if listing.room_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Listing must be linked to a room before it can be published")
    if listing.state == "PUBLISHED":
        listing.auto_approved_at_publish = False
        return listing
    if listing.state in ("SUSPENDED", "QUARANTINED") and admin.role != "super_admin":
        # Only a super admin imposed these (suspend_listing/quarantine_listing
        # are both super-admin-gated at the route level) -- only a super
        # admin may lift them, or a plain admin could silently undo a super
        # admin's enforcement action by republishing.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Only a super admin can republish a {listing.state.lower()} listing",
        )

    version = listing.current_draft_version or _create_new_version(db, listing)
    # Transient (not a mapped column, same pattern as annotate_availability's
    # `.available`) -- lets the route record the implicit approval as its own
    # distinct audit/domain event (5.1: "Approval and publication must be
    # distinct events even if executed milliseconds apart") without this
    # function needing to know about audit/event plumbing itself.
    listing.auto_approved_at_publish = version.approval_status != "APPROVED"
    if listing.auto_approved_at_publish:
        version.approval_status = "APPROVED"
        version.approved_at = datetime.now(timezone.utc)
        _record_approval_decision(db, version, "APPROVED", admin, decision_reason_code="publish_admin_decision")
    listing.current_public_version_id = version.id

    listing.rejection_reason = ""
    listing.state = "PUBLISHED"
    listing.paused_at = None
    if listing.published_at is None:
        listing.published_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(listing)

    user = get_user_by_party_id(db, listing.party_id)
    if user:
        notification_crud.notify_user(
            db, user.id,
            title="Listing approved and published",
            message=f'Your listing "{listing.name}" has been approved and published.',
            notification_type="listing.published",
            related_entity_type="listing", related_entity_id=listing.id,
        )
        db.commit()
        send_listing_published_email(user.email, user.full_name, listing.name)
    return listing


def reject_listing(db: Session, listing: Listing, reason: str, admin: AdminUser) -> Listing:
    """Admin/super-admin only (enforced at the route level). Only a listing that
    was actually submitted for review can be rejected. Rejecting the listing
    also rejects its pending draft version -- current_public_version_id is
    left untouched, so any previously published content stays live exactly as
    it was (a rejected resubmission never un-publishes an already-approved
    version, per ZR-ENG-CLR-001 Rule 3)."""
    if listing.state != "REVIEW":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a listing pending review can be rejected")
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A rejection reason is required")

    listing.state = "REJECTED"
    listing.rejection_reason = reason.strip()

    version = listing.current_draft_version
    if version is not None and version.approval_status != "APPROVED":
        version.approval_status = "REJECTED"
        _record_approval_decision(db, version, "REJECTED", admin, reason_note=listing.rejection_reason)

    db.commit()
    db.refresh(listing)

    user = get_user_by_party_id(db, listing.party_id)
    if user:
        notification_crud.notify_user(
            db, user.id,
            title="Listing not approved",
            message=f'Your listing "{listing.name}" was not approved. Reason: {listing.rejection_reason}',
            notification_type="listing.rejected",
            related_entity_type="listing", related_entity_id=listing.id,
        )
        db.commit()
        send_listing_rejected_email(user.email, user.full_name, listing.name, listing.rejection_reason)
    return listing


def request_changes_on_listing(db: Session, listing: Listing, reason: str, admin: AdminUser) -> Listing:
    """Rule 2 (5.1): 'Alternative review outcomes: CHANGES_REQUESTED, REJECTED,
    QUARANTINED, or SUSPENDED where appropriate.' Distinct from reject_listing
    -- CHANGES_REQUESTED signals 'fix these specific things and resubmit',
    not a full rejection; the host resubmits through the exact same
    submit_listing_for_review path as a DRAFT/REJECTED listing (see its
    updated guard). current_public_version_id is left untouched, same as
    reject_listing -- any previously published content stays live."""
    if listing.state != "REVIEW":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a listing pending review can have changes requested")
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A changes-requested reason is required")

    listing.state = "CHANGES_REQUESTED"
    listing.rejection_reason = reason.strip()

    version = listing.current_draft_version
    if version is not None and version.approval_status != "APPROVED":
        version.approval_status = "CHANGES_REQUESTED"
        _record_approval_decision(db, version, "CHANGES_REQUESTED", admin, reason_note=listing.rejection_reason)

    db.commit()
    db.refresh(listing)

    user = get_user_by_party_id(db, listing.party_id)
    if user:
        notification_crud.notify_user(
            db, user.id,
            title="Changes requested on your listing",
            message=f'"{listing.name}" needs changes before it can be approved. Reason: {listing.rejection_reason}',
            notification_type="listing.changes_requested",
            related_entity_type="listing", related_entity_id=listing.id,
        )
        db.commit()
    return listing
