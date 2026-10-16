"""The host's own decisions during a tenancy -- renter change requests,
host-proposed extensions / rent changes, and ending a tenancy. Each of these
previously existed only in the admin portal (an admin acting for the
provider); here the host makes them for their own properties. They reuse the
same engines (crud/booking_change_requests.py, crud/agreement_amendments.py,
crud/termination.py) with the host as the provider-side actor."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.correlation import get_correlation_id
from app.crud import agreement_amendments as amendment_crud
from app.crud import booking_change_requests as bcr_crud
from app.crud import occupancy as occupancy_crud
from app.crud import termination as termination_crud
from app.crud.audit import log_audit_event
from app.crud.party import party_id_for_listing
from app.models.leasing import Agreement
from app.models.user_account import UserAccount
from app.schemas.common import CamelModel
from app.schemas.habitability import HabitabilityIncidentRead
from app.schemas.host_refund import HostRefundMarkPaid, HostRefundRead
from app.schemas.leasing import (
    AgreementAmendmentRead,
    BookingChangeAlternativeProposalCreate,
    BookingChangeDecisionRequest,
    BookingChangeRequestRead,
)
from app.schemas.termination import (
    TerminationCaseCreate,
    TerminationCaseRead,
    TerminationExternalDecisionCreate,
    TerminationNoticeServiceRecord,
    TerminationResponseCreate,
)

router = APIRouter(prefix="/api/users/hosting", tags=["host-tenancy"], dependencies=[Depends(get_current_user)])


class HostAmendmentCreate(CamelModel):
    monthly_rent: float | None = Field(default=None, gt=0)
    additional_term_months: int | None = Field(default=None, ge=1, le=60)
    reason: str = Field(default="", max_length=1000)


def _audit(db: Session, request: Request, user: UserAccount, action: str, resource_type: str, resource_id, reason: str = ""):
    log_audit_event(
        db, None, action, resource_type, str(resource_id), get_correlation_id(request),
        reason=f"host_user:{user.id}" + (f" {reason}" if reason else ""),
    )


def _own_agreement_or_404(db: Session, agreement_id: int, user: UserAccount) -> Agreement:
    agreement = db.get(Agreement, agreement_id)
    if agreement is None or not user.party_id or party_id_for_listing(agreement.offer.listing) != user.party_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agreement not found")
    return agreement


def _own_occupancy_or_403(db: Session, occupancy_id: int, user: UserAccount):
    occupancy = occupancy_crud.get_occupancy_or_404(db, occupancy_id)
    if not user.party_id or party_id_for_listing(occupancy.listing) != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This occupancy does not belong to your property")
    return occupancy


# --- Renter change requests --------------------------------------------------


@router.get("/change-requests", response_model=list[BookingChangeRequestRead])
def list_change_requests(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return [bcr_crud.to_booking_change_request_read(b) for b in bcr_crud.list_change_requests_for_host(db, user)]


@router.post("/change-requests/{bcr_id}/approve", response_model=BookingChangeRequestRead)
def approve_change_request(
    bcr_id: int, payload: BookingChangeDecisionRequest, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Approving a term/rent change generates a new agreement version both
    sides re-sign; the current agreement stays in force until then."""
    bcr = bcr_crud.get_booking_change_request_or_404(db, bcr_id)
    updated = bcr_crud.approve_change_request(db, bcr, user, decision_note=payload.decision_note)
    _audit(db, request, user, "booking_change_request.approve", "booking_change_request", bcr_id)
    db.commit()
    return bcr_crud.to_booking_change_request_read(updated)


@router.post("/change-requests/{bcr_id}/decline", response_model=BookingChangeRequestRead)
def decline_change_request(
    bcr_id: int, payload: BookingChangeDecisionRequest, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    bcr = bcr_crud.get_booking_change_request_or_404(db, bcr_id)
    updated = bcr_crud.decline_change_request(db, bcr, user, decision_note=payload.decision_note)
    _audit(db, request, user, "booking_change_request.decline", "booking_change_request", bcr_id)
    db.commit()
    return bcr_crud.to_booking_change_request_read(updated)


@router.post("/change-requests/{bcr_id}/propose-alternative", response_model=BookingChangeRequestRead)
def propose_alternative(
    bcr_id: int, payload: BookingChangeAlternativeProposalCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    bcr = bcr_crud.get_booking_change_request_or_404(db, bcr_id)
    updated = bcr_crud.propose_alternative_terms(
        db, bcr, user,
        proposed_start_date=payload.proposed_start_date, additional_term_months=payload.additional_term_months,
        proposed_monthly_rent=payload.proposed_monthly_rent, decision_note=payload.decision_note,
    )
    _audit(db, request, user, "booking_change_request.propose_alternative", "booking_change_request", bcr_id, payload.decision_note)
    db.commit()
    return bcr_crud.to_booking_change_request_read(updated)


# --- Host-proposed amendments -----------------------------------------------


@router.get("/agreements/{agreement_id}/amendments", response_model=list[AgreementAmendmentRead])
def list_amendments(agreement_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return amendment_crud.list_amendments(db, _own_agreement_or_404(db, agreement_id, user))


@router.post(
    "/agreements/{agreement_id}/amendments", response_model=AgreementAmendmentRead, status_code=status.HTTP_201_CREATED,
)
def propose_amendment(
    agreement_id: int, payload: HostAmendmentCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Extension (additionalTermMonths) and/or rent change (monthlyRent) on a
    signed agreement. Produces a new version for both sides to sign."""
    agreement = _own_agreement_or_404(db, agreement_id, user)
    amendment = amendment_crud.propose_provider_amendment(
        db, agreement, user, monthly_rent=payload.monthly_rent,
        additional_term_months=payload.additional_term_months, reason=payload.reason,
    )
    _audit(db, request, user, "agreement.amendment_proposed", "agreement", agreement_id, payload.reason)
    db.commit()
    return amendment


# --- Ending a tenancy --------------------------------------------------------


@router.get("/occupancies/{occupancy_id}/termination-cases", response_model=list[TerminationCaseRead])
def list_termination_cases(occupancy_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    occupancy = _own_occupancy_or_403(db, occupancy_id, user)
    cases = termination_crud.list_termination_cases_for_occupancy(db, occupancy)
    # Host-facing view: sensitive renter notes are redacted (Section 21.1).
    return [termination_crud.to_termination_case_read(c, redact_notes=True) for c in cases]


@router.post(
    "/occupancies/{occupancy_id}/termination-cases", response_model=TerminationCaseRead, status_code=status.HTTP_201_CREATED,
)
def start_termination(
    occupancy_id: int, payload: TerminationCaseCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Start ending the tenancy -- a mutual surrender proposal the renter must
    accept, or a host ground that resolves or goes to review per the market's
    rules (models/termination_case.py)."""
    occupancy = _own_occupancy_or_403(db, occupancy_id, user)
    case = termination_crud.open_host_termination_case(db, occupancy, user, payload)
    _audit(db, request, user, "termination_case.open", "termination_case", case.id, payload.cause_code)
    db.commit()
    return termination_crud.to_termination_case_read(case, redact_notes=False)


@router.post("/termination-cases/{case_id}/accept-surrender", response_model=TerminationCaseRead)
def accept_surrender(case_id: int, request: Request, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """The host agreeing to a renter-proposed end date."""
    case = termination_crud.get_termination_case_or_404(db, case_id)
    updated = termination_crud.accept_mutual_surrender(db, case, host_user=user)
    _audit(db, request, user, "termination_case.accept_surrender", "termination_case", case_id)
    db.commit()
    return termination_crud.to_termination_case_read(updated, redact_notes=True)


@router.post("/termination-cases/{case_id}/decline-surrender", response_model=TerminationCaseRead)
def decline_surrender(case_id: int, request: Request, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    case = termination_crud.get_termination_case_or_404(db, case_id)
    updated = termination_crud.decline_mutual_surrender(db, case, host_user=user)
    _audit(db, request, user, "termination_case.decline_surrender", "termination_case", case_id)
    db.commit()
    return termination_crud.to_termination_case_read(updated, redact_notes=True)


@router.post("/termination-cases/{case_id}/notice-proof", response_model=TerminationCaseRead)
def attach_notice_proof(
    case_id: int, payload: TerminationNoticeServiceRecord, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Proof of off-portal delivery for a notice the host gave."""
    case = termination_crud.get_termination_case_or_404(db, case_id)
    updated = termination_crud.attach_notice_proof(db, case, proof_ref=payload.proof_ref, served_at=payload.served_at, host_user=user)
    _audit(db, request, user, "termination_case.notice_proof_attached", "termination_case", case_id)
    db.commit()
    return termination_crud.to_termination_case_read(updated, redact_notes=True)


@router.post("/termination-cases/{case_id}/acknowledge", response_model=TerminationCaseRead)
def acknowledge_renter_notice(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """The host confirming they received the renter's notice."""
    case = termination_crud.get_termination_case_or_404(db, case_id)
    return termination_crud.to_termination_case_read(termination_crud.acknowledge_notice(db, case, host_user=user), redact_notes=True)


@router.post("/termination-cases/{case_id}/respond", response_model=TerminationCaseRead)
def respond_to_termination(
    case_id: int, payload: TerminationResponseCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Accept, counter (with another date) or contest a renter's proposed end date."""
    from app.crud import termination_responses

    case = termination_crud.get_termination_case_or_404(db, case_id)
    updated = termination_responses.respond(
        db, case, response=payload.response, host_user=user, proposed_date=payload.proposed_date, note=payload.note,
    )
    _audit(db, request, user, f"termination_case.{payload.response.lower()}", "termination_case", case_id)
    db.commit()
    return termination_crud.to_termination_case_read(updated, redact_notes=True)


@router.post("/termination-cases/{case_id}/external-decision", response_model=TerminationCaseRead)
def record_external_termination_decision(
    case_id: int, payload: TerminationExternalDecisionCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Record a court/tribunal order that settles a contested case."""
    from app.crud import termination_responses

    case = termination_crud.get_termination_case_or_404(db, case_id)
    updated = termination_responses.record_external_decision(
        db, case, outcome=payload.outcome, order_ref=payload.order_ref, effective_date=payload.effective_date,
        liability_amount=payload.liability_amount, host_user=user,
    )
    _audit(db, request, user, "termination_case.external_decision", "termination_case", case_id, payload.order_ref)
    db.commit()
    return termination_crud.to_termination_case_read(updated, redact_notes=True)


@router.post("/termination-cases/{case_id}/withdraw", response_model=TerminationCaseRead)
def withdraw_termination(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """Withdraw a case the host opened, while it's still undecided."""
    from app.crud import termination_responses

    case = termination_crud.get_termination_case_or_404(db, case_id)
    return termination_crud.to_termination_case_read(termination_responses.withdraw_own(db, case, host_user=user), redact_notes=True)


# --- Refunds the host owes (crud/tenancy_settlement.py) ------------------------


@router.get("/refunds", response_model=list[HostRefundRead])
def list_owed_refunds(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.crud import tenancy_settlement

    return tenancy_settlement.list_for_host(db, user)


@router.post("/refunds/{refund_id}/mark-paid", response_model=HostRefundRead)
def mark_refund_paid(
    refund_id: int, payload: HostRefundMarkPaid, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    from app.crud import tenancy_settlement

    refund = tenancy_settlement.host_refund_for_host(db, refund_id, user)
    updated = tenancy_settlement.mark_paid(db, refund, payload.reference)
    _audit(db, request, user, "host_refund.marked_paid", "host_refund", refund_id, payload.reference)
    db.commit()
    return updated


# --- Habitability issues the renter reported (crud/habitability_incident.py) ---


class _FixReport(CamelModel):
    notes: str = Field(default="", max_length=2000)


@router.get("/habitability-incidents", response_model=list[HabitabilityIncidentRead])
def list_habitability_incidents(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    from sqlalchemy import select

    from app.models.habitability_incident import HabitabilityIncident
    from app.models.property import Property
    from app.models.room import Room

    if not user.party_id:
        return []
    return list(db.scalars(
        select(HabitabilityIncident).join(Room, Room.id == HabitabilityIncident.room_id)
        .join(Property, Property.id == Room.property_id).where(Property.owner_party_id == user.party_id)
        .order_by(HabitabilityIncident.opened_at.desc())
    ))


@router.post("/habitability-incidents/{incident_id}/acknowledge", response_model=HabitabilityIncidentRead)
def acknowledge_habitability_incident(incident_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.crud import habitability_incident as crud

    return crud.acknowledge(db, crud.get_habitability_incident_or_404(db, incident_id), user)


@router.post("/habitability-incidents/{incident_id}/fixed", response_model=HabitabilityIncidentRead)
def report_habitability_fixed(
    incident_id: int, payload: _FixReport, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    from app.crud import habitability_incident as crud

    return crud.report_fixed(db, crud.get_habitability_incident_or_404(db, incident_id), user, payload.notes)
