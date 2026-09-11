"""ZR-ENG-CLR-006 Section 9: Unavailable/Unsafe/Uninhabitable Accommodation.
H2/H3 severities freeze the room by flipping Room.status to "inactive" --
the same field crud/listing.py:is_listing_available/annotate_availability
already check, so this reuses an existing, already-tested availability gate
rather than inventing a second one. Resolving the incident restores it, but
only when no other open H2/H3 incident remains for the same room."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.crud.party import assert_provider_access, party_id_for_room
from app.models.admin_user import AdminUser
from app.models.finance import Obligation
from app.models.guest import Guest
from app.models.leasing import Agreement
from app.models.habitability_incident import (
    HABITABILITY_SEVERITIES,
    ROOM_FREEZING_SEVERITIES,
    HabitabilityIncident,
)
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.schemas.finance import RefundDecide, RefundRequestCreate
from app.schemas.habitability import HabitabilityCreditApply, HabitabilityIncidentCreate, HabitabilityIncidentResolve


def report_habitability_incident(
    db: Session, occupancy: Occupancy, data: HabitabilityIncidentCreate, *, guest: Guest | None = None, admin: AdminUser | None = None,
) -> HabitabilityIncident:
    """ZR-ENG-CLR-006 Section 9.1: exactly one of guest/admin identifies the
    reporter. A renter can only report against their own active occupancy;
    an admin must have provider access to the room."""
    if occupancy.status != "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an active occupancy can have a habitability incident reported")
    if data.severity not in HABITABILITY_SEVERITIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unrecognized severity '{data.severity}'")

    if guest is not None:
        if occupancy.guest_id != guest.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This occupancy does not belong to you")
    elif admin is not None:
        assert_provider_access(db, admin, party_id_for_room(occupancy.room))
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A reporter is required")

    incident = HabitabilityIncident(
        room_id=occupancy.room_id,
        occupancy_id=occupancy.id,
        reported_by_guest_id=guest.id if guest else None,
        reported_by_admin_id=admin.id if admin else None,
        severity=data.severity,
        description=data.description,
    )
    db.add(incident)

    # ZR-ENG-CLR-006 Section 9.1: "H2/H3 -> Freeze new booking." Never touches
    # the active Occupancy itself -- only new-booking visibility.
    if data.severity in ROOM_FREEZING_SEVERITIES:
        occupancy.room.status = "inactive"

    db.commit()
    db.refresh(incident)

    listing = db.scalar(select(Listing).where(Listing.room_id == occupancy.room_id))
    if guest is not None:
        if listing and listing.party_id:
            notif_crud.notify_user_by_party(
                db, listing.party_id,
                title="A habitability issue was reported",
                message=f'A {data.severity} habitability issue was reported for "{listing.name if listing else "your listing"}".',
                notification_type="habitability_incident.opened",
                related_entity_type="habitability_incident", related_entity_id=str(incident.id),
            )
    else:
        notif_crud.notify_user_by_guest(
            db, occupancy.guest,
            title="A habitability issue was reported for your home",
            message=f"Your host reported a {data.severity} habitability issue for your tenancy.",
            notification_type="habitability_incident.opened",
            related_entity_type="habitability_incident", related_entity_id=str(incident.id),
        )
    return incident


def resolve_habitability_incident(
    db: Session, incident: HabitabilityIncident, admin: AdminUser, data: HabitabilityIncidentResolve,
) -> HabitabilityIncident:
    assert_provider_access(db, admin, party_id_for_room(incident.room))
    if incident.status != "OPEN":
        raise HTTPException(status.HTTP_409_CONFLICT, "This incident has already been resolved")

    incident.status = "RESOLVED"
    incident.resolved_at = datetime.now(timezone.utc)
    incident.resolved_by_admin_id = admin.id
    incident.resolution_notes = data.resolution_notes

    if incident.severity in ROOM_FREEZING_SEVERITIES:
        other_open_severe = db.scalar(
            select(HabitabilityIncident).where(
                HabitabilityIncident.room_id == incident.room_id,
                HabitabilityIncident.status == "OPEN",
                HabitabilityIncident.severity.in_(ROOM_FREEZING_SEVERITIES),
                HabitabilityIncident.id != incident.id,
            )
        )
        if other_open_severe is None:
            incident.room.status = "active"

    db.commit()
    db.refresh(incident)
    return incident


def apply_habitability_credit(
    db: Session, incident: HabitabilityIncident, admin: AdminUser, data: HabitabilityCreditApply,
) -> HabitabilityIncident:
    """ZR-ENG-CLR-006 Section 9.1 H1 row: 'Compliance review; possible rent
    adjustment/credit.' Executed as a real Section 5 RefundRequest against a
    specific already-paid RENT obligation on this incident's own occupancy
    -- reusing the exact tested pattern crud/refund_entitlement.py:
    execute_refund_entitlement already uses, since 'credit' means the same
    thing here: money actually given back, not a bookkeeping note. One-shot
    per incident (a second call is rejected, not accumulated) -- see the
    model's own field docstring for why this is deliberately H1-only."""
    from app.crud import finance as finance_crud  # local import: finance.py imports this module at module level

    assert_provider_access(db, admin, party_id_for_room(incident.room))
    if incident.severity != "H1":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A habitability credit can only be applied to an H1 incident")
    if incident.credited_amount > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "A credit has already been applied to this incident")
    if data.amount <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The credit amount must be positive")
    if not data.reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A reason is required to apply a habitability credit")

    obligation = db.get(Obligation, data.obligation_id)
    # RENT obligations reach an occupancy via either occupancy_id (generated
    # while ACTIVE) or the agreement's own obligations (the initial one, set
    # at signing) -- same duality crud/refund_entitlement.py:
    # _rent_obligations_for_case already resolves for this exact reason.
    agreement = db.scalar(select(Agreement).where(Agreement.offer_id == incident.occupancy.offer_id))
    valid_obligation_ids = {o.id for o in incident.occupancy.obligations}
    if agreement:
        valid_obligation_ids |= {o.id for o in agreement.obligations}
    if obligation is None or obligation.id not in valid_obligation_ids or obligation.obligation_type != "RENT":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The obligation must be a RENT obligation on this incident's own occupancy")
    paid_allocation = next((a for a in obligation.allocations if a.amount_allocated > 0), None)
    if paid_allocation is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This obligation has no paid amount to credit against")

    refund = finance_crud.request_refund(
        db,
        RefundRequestCreate(
            payment_id=paid_allocation.payment_id, obligation_id=obligation.id, amount=data.amount,
            reason=f"ZR-ENG-CLR-006 Section 9.1 H1 credit for habitability incident #{incident.id}: {data.reason}",
            idempotency_key=f"habitability-credit-{incident.id}",
        ),
        admin,
    )
    if refund.status == "REQUESTED":
        refund = finance_crud.decide_refund(db, refund, admin, RefundDecide(approve=True))
        finance_crud.reverse_platform_fee_for_refund(db, obligation, refund)

    incident.credited_amount = data.amount
    incident.credited_refund_request_id = refund.id
    incident.credit_reason = data.reason
    incident.credited_by_admin_id = admin.id
    incident.credited_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(incident)

    notif_crud.notify_user_by_guest(
        db, incident.occupancy.guest,
        title="A rent credit was applied to your tenancy",
        message=f"A credit of {refund.payment.currency} {data.amount:.2f} was applied following your habitability report.",
        notification_type="habitability_incident.credited",
        related_entity_type="habitability_incident", related_entity_id=str(incident.id),
    )
    return incident


def get_habitability_incident_or_404(db: Session, incident_id: int) -> HabitabilityIncident:
    incident = db.get(HabitabilityIncident, incident_id)
    if not incident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Habitability incident not found")
    return incident


def list_habitability_incidents_for_occupancy(db: Session, occupancy: Occupancy) -> list[HabitabilityIncident]:
    return list(
        db.scalars(
            select(HabitabilityIncident)
            .where(HabitabilityIncident.occupancy_id == occupancy.id)
            .order_by(HabitabilityIncident.opened_at.desc())
        )
    )


def list_habitability_incidents_for_admin(db: Session, admin: AdminUser) -> list[HabitabilityIncident]:
    """ZR-ENG-CLR-006 Section 9: the Host's incident inbox, across every room
    they provide -- same provider-ownership scoping as
    crud/termination.py:list_termination_cases_for_admin."""
    query = select(HabitabilityIncident).order_by(HabitabilityIncident.opened_at.desc())
    if admin.role != "super_admin":
        query = query.join(Listing, Listing.room_id == HabitabilityIncident.room_id).where(Listing.owner_id == admin.id)
    return list(db.scalars(query))


def has_open_severe_incident_for_room(db: Session, room_id: int) -> bool:
    """ZR-ENG-CLR-006 Section 9.2: 'Do not release disputed Host payout
    amounts while the related habitability liability is unresolved.' Read by
    crud/finance.py::run_payout as a new eligibility gate."""
    return db.scalar(
        select(HabitabilityIncident.id).where(
            HabitabilityIncident.room_id == room_id,
            HabitabilityIncident.status == "OPEN",
            HabitabilityIncident.severity.in_(ROOM_FREEZING_SEVERITIES),
        ).limit(1)
    ) is not None
