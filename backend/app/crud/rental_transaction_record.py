"""Rental Transaction Record wireframe: a computed, read-only composite read
model. The validated Occupancy is the sole root -- every section below is
reached only by following its own existing FK relationships forward (never
an independently supplied payment_id/agreement_id/guest_id from the caller),
mirroring crud/finance.py:get_payment_timeline's own "merge existing,
already-queryable records into one chronological view, build nothing new"
approach. Nothing this module returns is persisted; each field is read
straight from its own authoritative table."""

from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.crud.activation_gate import activation_decisions_for, handover_events_for
from app.crud.agreement_amendments import list_amendments
from app.crud.authority import get_valid_authority_for_room
from app.crud.finance import annotate_payment_context, to_deposit_record_read, to_obligation_read
from app.crud.identity_verification import get_valid_identity_credential
from app.crud.leasing import to_application_read
from app.crud.occupancy import to_occupancy_read
from app.crud.property_verification import effective_verification_status, get_valid_property_verification_for_room
from app.crud.sublet import to_sublet_request_read
from app.crud.termination import list_termination_cases_for_occupancy
from app.models.authority_record import AuthorityRecord
from app.models.domain_event import DomainEvent
from app.models.finance import Obligation, PaymentAllocation, SimulatedPayment
from app.models.identity_verification import IdentityVerification
from app.models.occupancy import Occupancy
from app.models.property_verification import PropertyVerification
from app.models.sublet_request import SubletRequest
from app.models.termination_record import TerminationRecord
from app.schemas.finance import SimulatedPaymentRead
from app.schemas.rental_transaction_record import RentalTransactionRecordRead, RentalTransactionTimelineEntryRead
from app.schemas.verification import PROPERTY_VERIFICATION_SHARING_SCOPE, RenterVerificationStatusItem

_AUTHORITY_TO_LIST_SHARING_SCOPE = "Visible to admins and the property's own Host. Never exposed to renters."
_IDENTITY_SHARING_SCOPE = "Visible to you and admins. Never visible to a Host."


def _obligations_for(db: Session, occupancy: Occupancy, agreement) -> list[Obligation]:
    """The initial RENT+DEPOSIT obligations are created at agreement-signing
    time, before Occupancy exists, so they carry agreement_id, not
    occupancy_id (see crud/leasing.py:create_agreement); recurring rent
    obligations generated after move-in carry occupancy_id instead (see
    crud/occupancy.py:generate_next_rent_obligation). Both must be included
    for a complete financial history of this rental."""
    filters = [Obligation.occupancy_id == occupancy.id]
    if agreement is not None:
        filters.append(Obligation.agreement_id == agreement.id)
    return list(db.scalars(select(Obligation).where(or_(*filters)).order_by(Obligation.due_date)))


def _payments_for(db: Session, obligation_ids: list[int]) -> list[SimulatedPayment]:
    if not obligation_ids:
        return []
    return list(
        db.scalars(
            select(SimulatedPayment)
            .join(PaymentAllocation, PaymentAllocation.payment_id == SimulatedPayment.id)
            .where(PaymentAllocation.obligation_id.in_(obligation_ids))
            .distinct()
            .order_by(SimulatedPayment.created_at)
        )
    )


def _timeline_for(
    db: Session, *, application, agreement, occupancy: Occupancy, payments: list[SimulatedPayment], sublet_requests: list[SubletRequest],
) -> list[RentalTransactionTimelineEntryRead]:
    """Same merge-existing-DomainEvent-rows-into-one-chronological-view
    technique as crud/finance.py:get_payment_timeline -- generalized across
    this rental's own resource types/ids instead of one payment."""
    resource_pairs: list[tuple[str, str]] = [("occupancy", str(occupancy.id))]
    if application is not None:
        resource_pairs.append(("application", str(application.id)))
        if application.offer is not None:
            resource_pairs.append(("offer", str(application.offer.id)))
    if agreement is not None:
        resource_pairs.append(("agreement", str(agreement.id)))
    for payment in payments:
        resource_pairs.append(("simulated_payment", str(payment.id)))
    for sublet_request in sublet_requests:
        resource_pairs.append(("sublet_request", str(sublet_request.id)))

    conditions = [
        (DomainEvent.resource_type == resource_type) & (DomainEvent.resource_id == resource_id)
        for resource_type, resource_id in resource_pairs
    ]
    events = list(db.scalars(select(DomainEvent).where(or_(*conditions)).order_by(DomainEvent.occurred_at)))
    return [
        RentalTransactionTimelineEntryRead(
            timestamp=event.occurred_at, source=event.resource_type.upper(), event_type=event.event_type, detail=event.payload,
        )
        for event in events
    ]


def build_rental_transaction_record(db: Session, occupancy: Occupancy, *, include_identity: bool = False) -> RentalTransactionRecordRead:
    """Builds the composite record at read time -- nothing here is persisted.
    include_identity must only ever be True when the viewer is the renter
    themselves (their own identity claim); a host viewer must never see it,
    per schemas/verification.py's own sharing_scope facts."""
    now = datetime.now(timezone.utc)

    offer = occupancy.offer
    application = offer.application if offer else None
    agreement = offer.agreement if offer else None

    obligations = _obligations_for(db, occupancy, agreement)
    obligation_ids = [o.id for o in obligations]
    payments = _payments_for(db, obligation_ids)

    deposit_obligation = next((o for o in obligations if o.obligation_type == "DEPOSIT"), None)
    deposit_record = deposit_obligation.deposit_record if deposit_obligation else None

    sublet_requests = list(
        db.scalars(
            select(SubletRequest)
            .where(SubletRequest.current_occupancy_id == occupancy.id)
            .order_by(SubletRequest.created_at.desc())
        )
    )
    termination_record = db.scalar(
        select(TerminationRecord).where(TerminationRecord.occupancy_id == occupancy.id).order_by(TerminationRecord.created_at.desc())
    )

    property_record = get_valid_property_verification_for_room(db, occupancy.room_id)
    if property_record:
        property_status = "verified"
    else:
        latest_property_submission = db.scalar(
            select(PropertyVerification)
            .where(PropertyVerification.room_id == occupancy.room_id)
            .order_by(PropertyVerification.id.desc())
        )
        property_status = (
            effective_verification_status(latest_property_submission, now) if latest_property_submission else "not_submitted"
        )
    property_verification = RenterVerificationStatusItem(
        requirement_code="PROPERTY_VERIFICATION",
        status=property_status,
        expires_at=property_record.expires_at if property_record else None,
        explanation=f"Confirms room #{occupancy.room_id}'s property/address is real and evidenced.",
        sharing_scope=PROPERTY_VERIFICATION_SHARING_SCOPE,
    )

    authority_record = get_valid_authority_for_room(db, occupancy.room_id)
    if authority_record:
        authority_status = "verified"
    else:
        latest_authority_submission = db.scalar(
            select(AuthorityRecord).where(AuthorityRecord.room_id == occupancy.room_id).order_by(AuthorityRecord.id.desc())
        )
        authority_status = (
            effective_verification_status(latest_authority_submission, now) if latest_authority_submission else "not_submitted"
        )
    authority_to_list = RenterVerificationStatusItem(
        requirement_code="AUTHORITY_TO_LIST",
        status=authority_status,
        expires_at=authority_record.expires_at if authority_record else None,
        explanation=f"Confirms the host has the right (owner, agent, or manager) to list room #{occupancy.room_id}.",
        sharing_scope=_AUTHORITY_TO_LIST_SHARING_SCOPE,
    )

    identity_verification = None
    if include_identity:
        user_account = occupancy.guest.user_account if occupancy.guest else None
        party_id = user_account.party_id if user_account else None
        if party_id:
            identity_credential = get_valid_identity_credential(db, party_id)
            latest_identity_submission = db.scalar(
                select(IdentityVerification).where(IdentityVerification.party_id == party_id).order_by(IdentityVerification.id.desc())
            )
            identity_status = "verified" if identity_credential else (
                latest_identity_submission.status if latest_identity_submission else "not_submitted"
            )
            identity_verification = RenterVerificationStatusItem(
                requirement_code="IDENTITY",
                status=identity_status,
                expires_at=identity_credential.expires_at if identity_credential else None,
                explanation="Confirms who the renter is.",
                sharing_scope=_IDENTITY_SHARING_SCOPE,
            )

    return RentalTransactionRecordRead(
        occupancy=to_occupancy_read(occupancy),
        application=to_application_read(application) if application else None,
        amendments=list_amendments(db, agreement) if agreement else [],
        obligations=[to_obligation_read(o) for o in obligations],
        payments=[SimulatedPaymentRead.model_validate(annotate_payment_context(p)) for p in payments],
        deposit=to_deposit_record_read(deposit_record) if deposit_record else None,
        handover_events=list(handover_events_for(db, occupancy.id)),
        activation_decisions=list(activation_decisions_for(db, occupancy.id)),
        sublet_requests=[to_sublet_request_read(db, sr) for sr in sublet_requests],
        termination_cases=list(list_termination_cases_for_occupancy(db, occupancy)),
        termination_record=termination_record,
        property_verification=property_verification,
        authority_to_list=authority_to_list,
        identity_verification=identity_verification,
        timeline=_timeline_for(db, application=application, agreement=agreement, occupancy=occupancy, payments=payments, sublet_requests=sublet_requests),
    )
