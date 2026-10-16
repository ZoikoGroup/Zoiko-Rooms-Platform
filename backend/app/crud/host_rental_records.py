"""The host's Rental Records list: one row per rental on a property the host's
party owns, from accepted offer (agreement stage, no occupancy yet) through
move-in, notice and the end of the tenancy.

Everything is derived from the real rows -- offer terms for rent/deposit/term,
the agreement's own signature timestamps for who still has to sign, rent
obligations for the payment summary, the renter's move-out handover notice or
an open termination case for the notice state. Batched per table, so the cost
doesn't grow with properties x rooms."""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.leasing import Agreement, Application, Offer
from app.models.listing import Listing
from app.models.occupancy import Occupancy, OccupancyCoTenant
from app.models.occupancy_activation import OccupancyHandoverEvent
from app.models.property import Property
from app.models.rental_payment import RentalPaymentObligation
from app.models.room import Room
from app.models.sublet_request import SubletRequest
from app.models.termination_case import TerminationCase
from app.models.user_account import UserAccount
from app.schemas.host_rental_record import (
    HostRentalRecordRead,
    RentalRecordNotice,
    RentalRecordPaymentSummary,
    RentalRecordSublet,
)

# An ACTIVE tenancy whose end date is this close is shown as "ending".
ENDING_WINDOW_DAYS = 30
# Termination cases that no longer affect the tenancy.
_CLOSED_CASE_STATUSES = ("SURRENDER_DECLINED", "REJECTED_PATHWAY", "WITHDRAWN")
_PAID = ("CONFIRMED",)
_CLOSED_OBLIGATION = ("CONFIRMED", "CANCELLED", "WAIVED", "REVERSED")
# Agreement still collecting signatures.
_SIGNING = ("SENT", "PARTIALLY_EXECUTED")
_DEAD_AGREEMENT = ("VOID", "EXPIRED")


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    days = [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(d.day, days))


def _latest_terms(offer: Offer):
    return offer.terms[-1] if offer.terms else None


def _payment_summary(obligations: list[RentalPaymentObligation], currency: str) -> RentalRecordPaymentSummary | None:
    if not obligations:
        return None
    today = date.today()
    open_obl = sorted((o for o in obligations if o.status not in _CLOSED_OBLIGATION), key=lambda o: o.due_date)
    overdue = [o for o in open_obl if o.status == "OVERDUE" or (o.status in ("UPCOMING", "DUE") and o.due_date < today)]
    nxt = next((o for o in open_obl if o.status != "DISPUTED"), None)
    return RentalRecordPaymentSummary(
        currency=obligations[0].currency or currency,
        paid_count=sum(1 for o in obligations if o.status in _PAID),
        open_count=len(open_obl),
        overdue_count=len(overdue),
        overdue_amount=float(sum(float(o.amount) for o in overdue)),
        disputed_count=sum(1 for o in obligations if o.status == "DISPUTED"),
        next_due_date=nxt.due_date if nxt else None,
        next_due_amount=float(nxt.amount) if nxt else None,
        next_due_status=nxt.status if nxt else None,
    )


def _notice_for(
    occupancy: Occupancy, cases: list[TerminationCase], events: list[OccupancyHandoverEvent],
) -> RentalRecordNotice | None:
    live_case = next((c for c in sorted(cases, key=lambda c: c.id, reverse=True) if c.status not in _CLOSED_CASE_STATUSES), None)
    if live_case is not None:
        return RentalRecordNotice(
            kind="TERMINATION_CASE",
            initiated_by="RENTER" if live_case.initiator_guest_id else "HOST",
            given_at=live_case.notice_created_at,
            effective_date=live_case.effective_termination_date or live_case.earliest_effective_date,
            status=live_case.status,
            termination_case_id=live_case.id,
            cause_code=live_case.cause_code,
            pending_responders=live_case.pending_responders,
            response_due_at=live_case.response_due_at,
        )
    notice = next((e for e in events if e.event_type == "MOVE_OUT_NOTICE_GIVEN"), None)
    if notice is not None:
        confirmed = any(e.event_type == "HOST_MOVE_OUT_CONFIRMED" for e in events)
        ready = any(e.event_type == "MOVE_OUT_READY" for e in events)
        return RentalRecordNotice(
            kind="MOVE_OUT_NOTICE",
            initiated_by="RENTER",
            given_at=notice.created_at,
            effective_date=occupancy.expected_end_date,
            status="HOST_CONFIRMED" if confirmed else "RENTER_VACATED" if ready else "GIVEN",
        )
    return None


def list_host_rental_records(db: Session, user: UserAccount) -> list[HostRentalRecordRead]:
    if not user.party_id:
        return []
    party_id = user.party_id

    room_rows = db.execute(
        select(Room, Property).join(Property, Property.id == Room.property_id).where(Property.owner_party_id == party_id)
    ).all()
    rooms = {room.id: (room, prop) for room, prop in room_rows}
    if not rooms:
        return []

    listings = {
        l.id: l for l in db.scalars(select(Listing).where(Listing.room_id.in_(rooms.keys())))
    }
    if not listings:
        return []

    offers = list(db.scalars(
        select(Offer)
        .options(
            selectinload(Offer.terms), selectinload(Offer.agreement), selectinload(Offer.guest),
            selectinload(Offer.application),
        )
        .outerjoin(Agreement, Agreement.offer_id == Offer.id)
        .where(Offer.listing_id.in_(listings.keys()), or_(Offer.status == "ACCEPTED", Agreement.id.is_not(None)))
    ))
    if not offers:
        return []
    offer_ids = [o.id for o in offers]

    occupancies = {
        occ.offer_id: occ
        for occ in db.scalars(
            select(Occupancy).options(selectinload(Occupancy.guest)).where(Occupancy.offer_id.in_(offer_ids))
        )
    }
    occ_ids = [o.id for o in occupancies.values()]
    agreement_ids = [o.agreement.id for o in offers if o.agreement]

    co_tenants: dict[int, list[str]] = defaultdict(list)
    cases: dict[int, list[TerminationCase]] = defaultdict(list)
    events: dict[int, list[OccupancyHandoverEvent]] = defaultdict(list)
    sublets: dict[int, list[SubletRequest]] = defaultdict(list)
    if occ_ids:
        for ct in db.scalars(select(OccupancyCoTenant).options(selectinload(OccupancyCoTenant.guest)).where(OccupancyCoTenant.occupancy_id.in_(occ_ids))):
            co_tenants[ct.occupancy_id].append(ct.guest.name if ct.guest else "Co-tenant")
        for c in db.scalars(select(TerminationCase).where(TerminationCase.occupancy_id.in_(occ_ids))):
            cases[c.occupancy_id].append(c)
        for e in db.scalars(
            select(OccupancyHandoverEvent).where(OccupancyHandoverEvent.occupancy_id.in_(occ_ids)).order_by(OccupancyHandoverEvent.id)
        ):
            events[e.occupancy_id].append(e)
        for s in db.scalars(
            select(SubletRequest).where(SubletRequest.current_occupancy_id.in_(occ_ids)).order_by(SubletRequest.id.desc())
        ):
            sublets[s.current_occupancy_id].append(s)

    obligations: dict[tuple[str, int], list[RentalPaymentObligation]] = defaultdict(list)
    if occ_ids or agreement_ids:
        conds = []
        if occ_ids:
            conds.append(RentalPaymentObligation.occupancy_id.in_(occ_ids))
        if agreement_ids:
            conds.append(RentalPaymentObligation.agreement_id.in_(agreement_ids))
        for ob in db.scalars(select(RentalPaymentObligation).where(or_(*conds))):
            if ob.occupancy_id:
                obligations[("occ", ob.occupancy_id)].append(ob)
            elif ob.agreement_id:
                obligations[("agr", ob.agreement_id)].append(ob)

    today = date.today()
    records: list[HostRentalRecordRead] = []
    for offer in offers:
        listing = listings[offer.listing_id]
        room, prop = rooms.get(listing.room_id, (None, None))
        agreement = offer.agreement
        occ = occupancies.get(offer.id)
        terms = _latest_terms(offer)
        currency = listing.currency or "GBP"

        start = (occ.move_in_date if occ and occ.move_in_date else None) or (terms.start_date if terms else None)
        end = (occ.expected_end_date if occ else None) or (
            _add_months(terms.start_date, terms.term_months) if terms and terms.start_date and terms.term_months else None
        )

        notice = _notice_for(occ, cases[occ.id], events[occ.id]) if occ else None
        sub = sublets[occ.id][0] if occ and sublets[occ.id] else None
        # Each obligation is filed under its occupancy if it has one, else its agreement.
        obl = (obligations[("occ", occ.id)] if occ else []) + (obligations[("agr", agreement.id)] if agreement else [])
        payments = _payment_summary(obl, currency)

        agreement_status = agreement.status if agreement else None
        # Signatures only matter before there's an occupancy (one exists only
        # once the agreement is executed).
        signing = occ is None and agreement is not None and agreement_status in _SIGNING
        host_must_sign = signing and not agreement.signed_by_provider_at
        renter_must_sign = signing and bool(agreement.signed_by_provider_at) and not agreement.signed_by_renter_at

        # Status
        if occ is None:
            if offer.status in ("DECLINED", "WITHDRAWN", "EXPIRED") or agreement_status in _DEAD_AGREEMENT:
                status = "cancelled"
            elif agreement_status == "SIGNED":
                status = "upcoming"
            else:
                status = "pending"
        elif occ.status == "PENDING_MOVE_IN":
            status = "upcoming"
        elif occ.status == "CANCELLED":
            status = "cancelled"
        elif occ.status == "ENDED":
            status = "terminated" if any(c.status == "TERMINATED" for c in cases[occ.id]) else "ended"
        elif notice is not None:
            status = "notice"
        elif end and end - today <= timedelta(days=ENDING_WINDOW_DAYS):
            status = "ending"
        else:
            status = "active"

        # Next action for the host
        if host_must_sign:
            next_action = "SIGN_AGREEMENT"
        elif renter_must_sign:
            next_action = "AWAITING_RENTER_SIGNATURE"
        elif status == "upcoming" and occ is not None:
            next_action = "PREPARE_MOVE_IN"
        elif notice and notice.kind == "TERMINATION_CASE" and notice.status == "SURRENDER_PROPOSED" and notice.initiated_by == "RENTER":
            next_action = "RESPOND_SURRENDER"
        elif notice and notice.status == "AWAITING_RESPONSE" and "HOST" in notice.pending_responders:
            next_action = "RESPOND_END_DATE"
        elif notice and notice.kind == "MOVE_OUT_NOTICE" and notice.status == "RENTER_VACATED":
            next_action = "CONFIRM_MOVE_OUT"
        elif notice and notice.status in ("GIVEN", "OPENED", "PENDING_REVIEW", "CONTESTED"):
            next_action = "REVIEW_NOTICE"
        elif payments and (payments.overdue_count or payments.disputed_count or payments.next_due_status == "RECIPIENT_CONFIRMATION_PENDING"):
            next_action = "REVIEW_PAYMENTS"
        else:
            next_action = "NONE"

        renter_name = (occ.guest.name if occ and occ.guest else None) or (offer.guest.name if offer.guest else "Renter")
        stamps = [offer.created_at, offer.accepted_at, agreement.created_at if agreement else None,
                  agreement.signed_by_provider_at if agreement else None, agreement.signed_by_renter_at if agreement else None,
                  occ.created_at if occ else None, occ.ended_at if occ else None,
                  notice.given_at if notice else None]
        updated_at = max((s if s.tzinfo else s.replace(tzinfo=timezone.utc)) for s in stamps if s is not None)

        records.append(HostRentalRecordRead(
            ref=f"ZR-R-{occ.id}" if occ else f"ZR-A-{offer.application_id}",
            occupancy_id=occ.id if occ else None,
            application_id=offer.application_id,
            offer_id=offer.id,
            agreement_id=agreement.id if agreement else None,
            listing_id=listing.id,
            listing_name=listing.name,
            property_id=prop.id if prop else None,
            property_address=f"{prop.address}, {prop.city}" if prop else "",
            room_id=room.id if room else None,
            room_type=room.room_type if room else "",
            has_ensuite=bool(room.has_ensuite) if room else False,
            renters=[renter_name, *co_tenants[occ.id]] if occ else [renter_name],
            status=status,
            occupancy_status=occ.status if occ else None,
            offer_status=offer.status,
            agreement_status=agreement_status,
            agreement_version=agreement.version if agreement else None,
            host_signed_at=agreement.signed_by_provider_at if agreement else None,
            renter_signed_at=agreement.signed_by_renter_at if agreement else None,
            monthly_rent=float(terms.monthly_rent) if terms else None,
            deposit_amount=float(terms.deposit_amount) if terms else None,
            currency=currency,
            term_months=terms.term_months if terms else None,
            cadence=terms.cadence if terms else None,
            start_date=start,
            end_date=end,
            move_out_date=occ.move_out_date if occ else None,
            next_action=next_action,
            payments=payments,
            notice=notice,
            sublet=RentalRecordSublet(id=sub.id, status=sub.status, created_at=sub.created_at) if sub else None,
            updated_at=updated_at,
        ))

    records.sort(key=lambda r: r.updated_at, reverse=True)
    return records
