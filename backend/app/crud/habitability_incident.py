"""ZR-ENG-CLR-006 Section 9: Unavailable/Unsafe/Uninhabitable Accommodation.
H2/H3 severities freeze the room by flipping Room.status to "inactive" --
the same field crud/listing.py:is_listing_available/annotate_availability
already check, so this reuses an existing, already-tested availability gate
rather than inventing a second one. Resolving the incident restores it, but
only when no other open H2/H3 incident remains for the same room."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.crud.events import emit_event
from app.crud.party import assert_provider_access, party_id_for_room
from app.models.admin_user import AdminUser
from app.models.guest import Guest
from app.models.habitability_incident import (
    HABITABILITY_SEVERITIES,
    OPEN_HABITABILITY_STATUSES,
    ROOM_FREEZING_SEVERITIES,
    HabitabilityIncident,
)
from app.models.user_account import UserAccount

# How long the host has to fix it, by severity (H3 = most serious).
_FIX_DAYS = {"H3": 2, "H2": 7, "H1": 14, "H0": 30}
# Share of the next unpaid rent credited to the renter when a serious
# problem isn't fixed in time.
_OVERDUE_CREDIT_SHARE = {"H3": 0.5, "H2": 0.25}
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.schemas.habitability import HabitabilityIncidentCreate


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
        fix_due_at=datetime.now(timezone.utc) + timedelta(days=_FIX_DAYS[data.severity]),
    )
    db.add(incident)

    # ZR-ENG-CLR-006 Section 9.1: "H2/H3 -> Freeze new booking." Never touches
    # the active Occupancy itself -- only new-booking visibility.
    if data.severity in ROOM_FREEZING_SEVERITIES:
        occupancy.room.status = "inactive"

    db.flush()
    # ZR-ENG-CLR-006 Section 20.2: property.habitability_incident_opened --
    # the one spec-named event this path had only as a notification, never
    # as a domain event.
    emit_event(
        db, "property.habitability_incident_opened", "habitability_incident", str(incident.id),
        {"roomId": occupancy.room_id, "occupancyId": occupancy.id, "severity": data.severity},
    )
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


def _close(db: Session, incident: HabitabilityIncident, *, basis: str, notes: str = "") -> None:
    """Resolved: reopens the room for new bookings when no other serious
    incident on it is still open."""
    incident.status = "RESOLVED"
    incident.resolved_at = datetime.now(timezone.utc)
    incident.resolution_basis = basis
    if notes:
        incident.resolution_notes = notes
    if incident.severity in ROOM_FREEZING_SEVERITIES:
        other_open_severe = db.scalar(
            select(HabitabilityIncident).where(
                HabitabilityIncident.room_id == incident.room_id,
                HabitabilityIncident.status.in_(OPEN_HABITABILITY_STATUSES),
                HabitabilityIncident.severity.in_(ROOM_FREEZING_SEVERITIES),
                HabitabilityIncident.id != incident.id,
            )
        )
        if other_open_severe is None:
            incident.room.status = "active"
            emit_event(
                db, "booking.inventory_reopened", "room", str(incident.room_id),
                {"habitabilityIncidentId": incident.id},
            )


def _assert_host(db: Session, incident: HabitabilityIncident, user: UserAccount) -> None:
    if not user.party_id or party_id_for_room(incident.room) != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This room isn't on your property")


def _assert_renter(incident: HabitabilityIncident, guest: Guest) -> None:
    if incident.occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This report isn't about your tenancy")


def _tell_renter(db: Session, incident: HabitabilityIncident, *, title: str, message: str, event: str) -> None:
    notif_crud.notify_user_by_guest(
        db, incident.occupancy.guest, title=title, message=message,
        notification_type=f"habitability_incident.{event}", related_entity_type="habitability_incident",
        related_entity_id=f"{incident.id}:{event}:{incident.fix_rejected_count}",
    )


def _tell_host(db: Session, incident: HabitabilityIncident, *, title: str, message: str, event: str) -> None:
    notif_crud.notify_user_by_party(
        db, party_id_for_room(incident.room), title=title, message=message,
        notification_type=f"habitability_incident.{event}", related_entity_type="habitability_incident",
        related_entity_id=f"{incident.id}:{event}:{incident.fix_rejected_count}",
    )


def acknowledge(db: Session, incident: HabitabilityIncident, user: UserAccount) -> HabitabilityIncident:
    _assert_host(db, incident, user)
    if incident.status != "OPEN":
        raise HTTPException(status.HTTP_409_CONFLICT, "This report has already been acknowledged")
    incident.status = "ACKNOWLEDGED"
    incident.acknowledged_at = datetime.now(timezone.utc)
    _tell_renter(
        db, incident, event="acknowledged", title="Your host is on it",
        message=f"Your host acknowledged the issue you reported. It's due to be fixed by {incident.fix_due_at.date().isoformat()}.",
    )
    db.commit()
    db.refresh(incident)
    return incident


def report_fixed(db: Session, incident: HabitabilityIncident, user: UserAccount, notes: str) -> HabitabilityIncident:
    _assert_host(db, incident, user)
    if incident.status not in ("OPEN", "ACKNOWLEDGED"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This report isn't waiting for a fix")
    incident.status = "FIX_REPORTED"
    incident.acknowledged_at = incident.acknowledged_at or datetime.now(timezone.utc)
    incident.fix_reported_at = datetime.now(timezone.utc)
    incident.resolution_notes = notes.strip()[:2000]
    _tell_renter(
        db, incident, event="fix_reported", title="Your host says it's fixed",
        message="Confirm the fix, or tell your host it isn't fixed. If you don't answer within a few days it's treated as fixed.",
    )
    db.commit()
    db.refresh(incident)
    return incident


def confirm_fixed(db: Session, incident: HabitabilityIncident, guest: Guest) -> HabitabilityIncident:
    _assert_renter(incident, guest)
    if incident.status != "FIX_REPORTED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Your host hasn't reported a fix yet")
    _close(db, incident, basis="RENTER_CONFIRMED")
    db.commit()
    db.refresh(incident)
    return incident


def reject_fix(db: Session, incident: HabitabilityIncident, guest: Guest, note: str) -> HabitabilityIncident:
    _assert_renter(incident, guest)
    if incident.status != "FIX_REPORTED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Your host hasn't reported a fix yet")
    incident.status = "ACKNOWLEDGED"
    incident.fix_reported_at = None
    incident.fix_rejected_count = (incident.fix_rejected_count or 0) + 1
    _tell_host(
        db, incident, event="fix_rejected", title="The renter says it isn't fixed",
        message=(note.strip()[:300] or "The issue is still there.") + f" Still due by {incident.fix_due_at.date().isoformat()}.",
    )
    db.commit()
    db.refresh(incident)
    return incident


def _confirm_days(incident: HabitabilityIncident) -> int:
    from app.services.policy import get_policy

    listing = incident.occupancy.listing
    return int(get_policy(listing.market_release if listing else None, "habitability.renter_confirm_days"))


def _apply_overdue_credit(db: Session, incident: HabitabilityIncident) -> bool:
    """A serious problem past its fix date: take a share of the next unpaid
    rent off, once per incident. Rent is paid to the host directly, so the
    credit is a reduction of what the renter owes, not a payout."""
    from app.models.rental_payment import RentalPaymentObligation

    share = _OVERDUE_CREDIT_SHARE.get(incident.severity)
    if not share or float(incident.credited_amount or 0) > 0:
        return False
    nxt = db.scalar(
        select(RentalPaymentObligation).where(
            RentalPaymentObligation.occupancy_id == incident.occupancy_id,
            RentalPaymentObligation.obligation_type == "RENT",
            RentalPaymentObligation.status.in_(("UPCOMING", "DUE")),
        ).order_by(RentalPaymentObligation.due_date)
    )
    if nxt is None:
        return False
    credit = round(float(nxt.amount) * share, 2)
    nxt.amount = round(float(nxt.amount) - credit, 2)
    incident.credited_amount = credit
    incident.credit_reason = f"{incident.severity} issue not fixed by {incident.fix_due_at.date().isoformat()}"
    incident.credited_at = datetime.now(timezone.utc)
    _tell_renter(
        db, incident, event="credited", title="Rent reduced: issue not fixed in time",
        message=(
            f"{nxt.currency} {credit:.2f} was taken off your rent due {nxt.due_date.isoformat()}. "
            "If the room is unlivable you can also end your tenancy (reason: property uninhabitable)."
        ),
    )
    _tell_host(
        db, incident, event="credited_for_host", title="Rent reduced: issue not fixed in time",
        message=f"{nxt.currency} {credit:.2f} was taken off the rent due {nxt.due_date.isoformat()}.",
    )
    return True


def apply_habitability_deadlines(db: Session) -> int:
    """Scheduler job: a reported fix the renter didn't answer is confirmed;
    a serious problem past its fix date gets the rent credit."""
    now = datetime.now(timezone.utc)
    changed = 0
    for incident in db.scalars(select(HabitabilityIncident).where(HabitabilityIncident.status.in_(OPEN_HABITABILITY_STATUSES))):
        if incident.status == "FIX_REPORTED" and incident.fix_reported_at is not None:
            reported = incident.fix_reported_at if incident.fix_reported_at.tzinfo else incident.fix_reported_at.replace(tzinfo=timezone.utc)
            if now - reported >= timedelta(days=_confirm_days(incident)):
                _close(db, incident, basis="DEEMED_CONFIRMED")
                changed += 1
        elif incident.fix_due_at is not None:
            due = incident.fix_due_at if incident.fix_due_at.tzinfo else incident.fix_due_at.replace(tzinfo=timezone.utc)
            if due < now and _apply_overdue_credit(db, incident):
                changed += 1
    db.commit()
    return changed


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
            HabitabilityIncident.status.in_(OPEN_HABITABILITY_STATUSES),
            HabitabilityIncident.severity.in_(ROOM_FREEZING_SEVERITIES),
        ).limit(1)
    ) is not None
