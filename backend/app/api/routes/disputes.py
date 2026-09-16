"""ZR-ENG-CLR-010 Section 4/9/10/11/12/13/19/20/21/22/23/24/25/26: renter
intake, host intake, the admin dispute-operations console, evidence
upload/redaction/legal-hold, external-proceeding filing/decision
recording, bilateral settlement negotiation (with lazy auto-expiry), case
reopen / internal review request (backed by a real, extendable deadline
record), financial-hold maker-checker, deadline tracking, moderated
case-room messaging (disabled party-to-party on safety cases or a specific
restricted party), dispute-party representation/authority tracking, the
canonical-records case export/chronology bundle, Section 12/20 dispute-
specialist RBAC (Support/Dispute Officer/Finance/Trust & Safety/
Legal-Compliance, see services/dispute_rbac.py), and Section 24 domain-event
emission (dispute.opened/claim.classified/evidence.received/hold.activated/
settlement.accepted/claim.referred/external.decision_recorded/claim.resolved/
dispute.closed, written via app/crud/events.py's pre-existing emit_event
outbox scaffold -- no consumer/dispatcher exists for this or any other
domain's events yet), QA-Q45 partial case closure while a claim awaits an
open external proceeding, the append-only DisputeDecision history read
route, and QA-Q16 evidence deletion requests (legal_hold as the one
exemption, see app/crud/dispute_evidence.py's request_deletion) for the
new general-purpose dispute domain (see app/models/dispute.py's own
docstring for why this is separate from the existing finance.DisputeCase
chargeback mechanism)."""

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, get_current_user
from app.core.correlation import get_correlation_id
from app.core.dispute_evidence_uploads import resolve_dispute_evidence_path
from app.crud import dispute_deadline as deadline_crud
from app.crud import dispute_evidence as evidence_crud
from app.crud import dispute_external_proceeding as proceeding_crud
from app.crud import dispute_message as message_crud
from app.crud import dispute_party as party_crud
from app.crud import dispute_settlement as settlement_crud
from app.crud import disputes as crud
from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.crud.guest import get_or_create_guest_for_user
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.dispute import DisputeResolutionCase, DisputeResolutionHold
from app.models.dispute_deadline import DisputeDeadline
from app.models.dispute_evidence import DisputeEvidenceItem
from app.models.dispute_external_proceeding import DisputeExternalProceeding
from app.models.dispute_message import DisputeCaseMessage
from app.models.dispute_party import DisputeParty
from app.models.dispute_settlement import DisputeSettlement
from app.models.user_account import UserAccount
from app.schemas.disputes import (
    DisputeCaseAssign,
    DisputeCaseClose,
    DisputeCaseCreate,
    DisputeCaseReopen,
    DisputeCaseRead,
    DisputeClaimCreate,
    DisputeClaimDecide,
    DisputeClaimRead,
    DisputeClaimReviewRequest,
    DisputeFinancialHoldCreate,
    DisputeFinancialHoldRead,
    DisputeFinancialHoldRelease,
    DisputeMoneyStatusByCurrency,
)
from app.schemas.dispute_decision import DisputeDecisionRead
from app.schemas.dispute_evidence import (
    DisputeEvidenceLegalHoldUpdate,
    DisputeEvidenceRead,
    DisputeEvidenceVerify,
    DisputeLegalHoldRead,
)
from app.schemas.dispute_external_proceeding import (
    DisputeExternalProceedingCreate,
    DisputeExternalProceedingDecide,
    DisputeExternalProceedingRead,
    DisputeExternalProceedingStatusUpdate,
)
from app.schemas.dispute_case_export import DisputeCaseExportRead
from app.schemas.dispute_deadline import DisputeDeadlineCreate, DisputeDeadlineExtend, DisputeDeadlineRead
from app.schemas.dispute_message import DisputeCaseMessageCreate, DisputeCaseMessageModerate, DisputeCaseMessageRead
from app.schemas.dispute_party import DisputePartyAddRepresentative, DisputePartyRead, DisputePartyRestrict
from app.schemas.dispute_settlement import DisputeSettlementCreate, DisputeSettlementRead, DisputeSettlementRespond
from app.services.dispute_case_export import build_case_export
from app.services.dispute_rbac import assert_dispute_role


def _to_evidence_read(db: Session, evidence: DisputeEvidenceItem) -> DisputeEvidenceRead:
    data = DisputeEvidenceRead.model_validate(evidence)
    return data.model_copy(update={"claim_ids": evidence_crud.claim_ids_for_evidence(db, evidence)})


def _evidence_file_response(evidence: DisputeEvidenceItem) -> FileResponse:
    # assert_evidence_downloadable already gates disclosure -- this only
    # narrows stored_filename (None for a text-only ADMIN_NOTE) so an admin
    # (who bypasses the disclosure check) can't be handed a path that was
    # never actually written to disk.
    if evidence.stored_filename is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This evidence item has no downloadable file")
    return FileResponse(resolve_dispute_evidence_path(evidence.stored_filename), media_type=evidence.content_type, filename=evidence.original_filename)


def _to_proceeding_read(db: Session, proceeding: DisputeExternalProceeding) -> DisputeExternalProceedingRead:
    data = DisputeExternalProceedingRead.model_validate(proceeding)
    return data.model_copy(update={"claim_ids": proceeding_crud.claim_ids_for_proceeding(db, proceeding)})


def _to_settlement_read(db: Session, settlement: DisputeSettlement) -> DisputeSettlementRead:
    data = DisputeSettlementRead.model_validate(settlement)
    return data.model_copy(update={"claim_ids": settlement_crud.claim_ids_for_settlement(db, settlement)})


def _to_deadline_read(deadline: DisputeDeadline) -> DisputeDeadlineRead:
    data = DisputeDeadlineRead.model_validate(deadline)
    return data.model_copy(
        update={"is_overdue": deadline_crud.is_overdue(deadline), "is_reminder_due": deadline_crud.is_reminder_due(deadline)},
    )


def _to_hold_read(hold: DisputeResolutionHold) -> DisputeFinancialHoldRead:
    data = DisputeFinancialHoldRead.model_validate(hold)
    return data.model_copy(update={"is_overdue_for_review": crud.is_hold_overdue_for_review(hold)})


def _to_case_read(case: DisputeResolutionCase) -> DisputeCaseRead:
    data = DisputeCaseRead.model_validate(case)
    money_status = [DisputeMoneyStatusByCurrency.model_validate(b) for b in crud.compute_money_status(case)]
    return data.model_copy(update={"money_status": money_status})


def _to_message_read(message: DisputeCaseMessage) -> DisputeCaseMessageRead:
    return DisputeCaseMessageRead.model_validate(message)


def _to_party_read(dispute_party: DisputeParty) -> DisputePartyRead:
    return DisputePartyRead.model_validate(dispute_party)


renter_router = APIRouter(prefix="/api/users/rentals/disputes", tags=["disputes-renter"], dependencies=[Depends(get_current_user)])
host_router = APIRouter(prefix="/api/users/hosting/disputes", tags=["disputes-host"], dependencies=[Depends(get_current_user)])
admin_router = APIRouter(prefix="/api/admin/disputes", tags=["disputes-admin"], dependencies=[Depends(get_current_admin)])


# -- Renter intake (Section 9) --------------------------------------------

@renter_router.post("", response_model=DisputeCaseRead, status_code=status.HTTP_201_CREATED)
def post_open_case_as_renter(
    payload: DisputeCaseCreate, request: Request, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.open_case(db, payload, guest=guest)
    log_audit_event(db, None, "dispute_case.open", "dispute_resolution_case", str(case.id), get_correlation_id(request), reason=f"guest:{guest.id}")
    emit_event(
        db, "dispute.opened", "dispute_resolution_case", str(case.id),
        {"caseId": case.id, "severity": case.severity, "primaryClaimFamily": case.primary_claim_family},
        correlation_id=get_correlation_id(request), actor_kind="guest", actor_id=str(guest.id), new_state=case.status,
    )
    db.commit()
    return _to_case_read(case)


@renter_router.get("", response_model=list[DisputeCaseRead])
def get_cases_as_renter(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    guest = get_or_create_guest_for_user(db, user)
    return crud.list_cases_for_guest(db, guest)


@renter_router.get("/{case_id}", response_model=DisputeCaseRead)
def get_case_as_renter(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    return _to_case_read(case)


@renter_router.get("/{case_id}/export", response_model=DisputeCaseExportRead)
def get_case_export_as_renter(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    return build_case_export(db, case, viewer_is_admin=False)


@renter_router.post("/{case_id}/evidence", response_model=DisputeEvidenceRead, status_code=status.HTTP_201_CREATED)
async def post_upload_evidence_as_renter(
    case_id: int, request: Request,
    file: UploadFile | None = File(default=None), note_text: str = Form(default=""), claim_ids: list[int] = Form(default=[]),
    captured_at: datetime | None = Form(default=None),
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    evidence = await evidence_crud.upload_evidence(
        db, case, file=file, note_text=note_text, claim_ids=claim_ids, guest=guest, captured_at=captured_at,
    )
    log_audit_event(db, None, "dispute_evidence.upload", "dispute_evidence_item", str(evidence.id), get_correlation_id(request), reason=f"guest:{guest.id}")
    emit_event(
        db, "evidence.received", "dispute_evidence_item", str(evidence.id),
        {"evidenceId": evidence.id, "caseId": case.id, "provenance": evidence.provenance},
        correlation_id=get_correlation_id(request), actor_kind="guest", actor_id=str(guest.id),
    )
    db.commit()
    return _to_evidence_read(db, evidence)


@renter_router.get("/{case_id}/evidence", response_model=list[DisputeEvidenceRead])
def get_evidence_as_renter(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    items = evidence_crud.list_evidence_for_case(db, case, viewer_is_admin=False)
    return [_to_evidence_read(db, e) for e in items]


@renter_router.get("/{case_id}/evidence/{evidence_id}/file")
def get_evidence_file_as_renter(
    case_id: int, evidence_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    evidence = evidence_crud.get_evidence_or_404(db, evidence_id)
    if evidence.case_id != case_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence item not found on this case")
    evidence_crud.assert_evidence_downloadable(evidence, guest=guest)
    return _evidence_file_response(evidence)


@renter_router.post("/{case_id}/evidence/{evidence_id}/delete", response_model=DisputeEvidenceRead)
def post_request_evidence_deletion_as_renter(
    case_id: int, evidence_id: int, request: Request, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    evidence = evidence_crud.get_evidence_or_404(db, evidence_id)
    if evidence.case_id != case_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence item not found on this case")
    updated = evidence_crud.request_deletion(db, evidence, guest=guest)
    log_audit_event(
        db, None, "dispute_evidence.delete_requested", "dispute_evidence_item", str(evidence_id), get_correlation_id(request),
        reason=f"guest:{guest.id}",
    )
    db.commit()
    return _to_evidence_read(db, updated)


@renter_router.get("/{case_id}/external-proceedings", response_model=list[DisputeExternalProceedingRead])
def get_external_proceedings_as_renter(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    return [_to_proceeding_read(db, p) for p in proceeding_crud.list_proceedings_for_case(db, case)]


@renter_router.post("/{case_id}/settlements", response_model=DisputeSettlementRead, status_code=status.HTTP_201_CREATED)
def post_propose_settlement_as_renter(
    case_id: int, payload: DisputeSettlementCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    settlement = settlement_crud.propose_settlement(
        db, case, guest=guest, claim_ids=payload.claim_ids, terms_text=payload.terms_text, amount=payload.amount,
        currency=payload.currency, expires_at=payload.expires_at,
        acknowledges_no_nonwaivable_waiver=payload.acknowledges_no_nonwaivable_waiver,
    )
    log_audit_event(db, None, "dispute_settlement.propose", "dispute_settlement", str(settlement.id), get_correlation_id(request), reason=f"guest:{guest.id}")
    db.commit()
    return _to_settlement_read(db, settlement)


@renter_router.get("/{case_id}/settlements", response_model=list[DisputeSettlementRead])
def get_settlements_as_renter(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    return [_to_settlement_read(db, s) for s in settlement_crud.list_settlements_for_case(db, case)]


@renter_router.get("/{case_id}/deadlines", response_model=list[DisputeDeadlineRead])
def get_deadlines_as_renter(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    return [_to_deadline_read(d) for d in deadline_crud.list_deadlines_for_case(db, case)]


@renter_router.post("/{case_id}/messages", response_model=DisputeCaseMessageRead, status_code=status.HTTP_201_CREATED)
def post_message_as_renter(
    case_id: int, payload: DisputeCaseMessageCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    message = message_crud.post_message(db, case, body=payload.body, guest=guest)
    log_audit_event(db, None, "dispute_message.post", "dispute_case_message", str(message.id), get_correlation_id(request), reason=f"guest:{guest.id}")
    db.commit()
    return _to_message_read(message)


@renter_router.get("/{case_id}/messages", response_model=list[DisputeCaseMessageRead])
def get_messages_as_renter(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    return [_to_message_read(m) for m in message_crud.list_messages_for_case(db, case, viewer_is_admin=False)]


@renter_router.get("/{case_id}/parties", response_model=list[DisputePartyRead])
def get_parties_as_renter(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    return [_to_party_read(p) for p in party_crud.list_parties_for_case(db, case)]


@renter_router.post("/{case_id}/settlements/{settlement_id}/respond", response_model=DisputeSettlementRead)
def post_respond_settlement_as_renter(
    case_id: int, settlement_id: int, payload: DisputeSettlementRespond, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    settlement = settlement_crud.get_settlement_or_404(db, settlement_id)
    if settlement.case_id != case_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Settlement not found on this case")
    updated = settlement_crud.respond_settlement(
        db, settlement, case, guest=guest, action=payload.action,
        counter_terms_text=payload.counter_terms_text, counter_amount=payload.counter_amount,
        counter_currency=payload.counter_currency, counter_expires_at=payload.counter_expires_at,
    )
    log_audit_event(db, None, "dispute_settlement.respond", "dispute_settlement", str(settlement_id), get_correlation_id(request), reason=payload.action)
    if payload.action == "ACCEPT":
        correlation_id = get_correlation_id(request)
        emit_event(
            db, "settlement.accepted", "dispute_settlement", str(updated.id),
            {"settlementId": updated.id, "caseId": case_id, "amount": float(updated.amount) if updated.amount is not None else None, "currency": updated.currency},
            correlation_id=correlation_id, actor_kind="guest", actor_id=str(guest.id), new_state=updated.status,
        )
        for linked_claim_id in settlement_crud.claim_ids_for_settlement(db, updated):
            emit_event(
                db, "claim.resolved", "dispute_resolution_claim", str(linked_claim_id),
                {"claimId": linked_claim_id, "caseId": case_id, "outcome": "SETTLED", "settlementId": updated.id},
                correlation_id=correlation_id, actor_kind="guest", actor_id=str(guest.id), new_state="SETTLED",
            )
    db.commit()
    return _to_settlement_read(db, updated)


@renter_router.post("/{case_id}/settlements/{settlement_id}/void", response_model=DisputeSettlementRead)
def post_void_settlement_as_renter(
    case_id: int, settlement_id: int, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    settlement = settlement_crud.get_settlement_or_404(db, settlement_id)
    if settlement.case_id != case_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Settlement not found on this case")
    updated = settlement_crud.void_settlement(db, settlement, guest=guest)
    log_audit_event(db, None, "dispute_settlement.void", "dispute_settlement", str(settlement_id), get_correlation_id(request))
    db.commit()
    return _to_settlement_read(db, updated)


@renter_router.post("/{case_id}/claims", response_model=DisputeClaimRead, status_code=status.HTTP_201_CREATED)
def post_add_claim_as_renter(
    case_id: int, payload: DisputeClaimCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    claim = crud.add_claim(db, case, payload, claimant_role="RENTER")
    log_audit_event(db, None, "dispute_claim.add", "dispute_resolution_claim", str(claim.id), get_correlation_id(request), reason=f"guest:{guest.id}")
    emit_event(
        db, "claim.classified", "dispute_resolution_claim", str(claim.id),
        {"claimId": claim.id, "caseId": case_id, "claimFamily": claim.claim_family, "authorityClass": claim.authority_class},
        correlation_id=get_correlation_id(request), actor_kind="guest", actor_id=str(guest.id),
    )
    db.commit()
    return claim


@renter_router.post("/{case_id}/claims/{claim_id}/request-review", response_model=DisputeClaimRead)
def post_request_review_as_renter(
    case_id: int, claim_id: int, payload: DisputeClaimReviewRequest, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    guest = get_or_create_guest_for_user(db, user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_guest_can_access_case(case, guest)
    claim = crud.get_claim_or_404(db, claim_id)
    updated = crud.request_internal_review(db, case, claim, reason=payload.reason)
    log_audit_event(db, None, "dispute_claim.request_review", "dispute_resolution_claim", str(claim_id), get_correlation_id(request), reason=f"guest:{guest.id}")
    db.commit()
    return updated


# -- Host intake (Section 10) ----------------------------------------------

def _host_party_id(user: UserAccount) -> int:
    if not user.party_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This account has no associated host party")
    return user.party_id


@host_router.post("", response_model=DisputeCaseRead, status_code=status.HTTP_201_CREATED)
def post_open_case_as_host(
    payload: DisputeCaseCreate, request: Request, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    party_id = _host_party_id(user)
    case = crud.open_case(db, payload, party_id=party_id)
    log_audit_event(db, None, "dispute_case.open", "dispute_resolution_case", str(case.id), get_correlation_id(request), reason=f"party:{party_id}")
    emit_event(
        db, "dispute.opened", "dispute_resolution_case", str(case.id),
        {"caseId": case.id, "severity": case.severity, "primaryClaimFamily": case.primary_claim_family},
        correlation_id=get_correlation_id(request), actor_kind="party", actor_id=str(party_id), new_state=case.status,
    )
    db.commit()
    return _to_case_read(case)


@host_router.get("", response_model=list[DisputeCaseRead])
def get_cases_as_host(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return crud.list_cases_for_party(db, _host_party_id(user))


@host_router.get("/{case_id}", response_model=DisputeCaseRead)
def get_case_as_host(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, _host_party_id(user))
    return _to_case_read(case)


@host_router.get("/{case_id}/export", response_model=DisputeCaseExportRead)
def get_case_export_as_host(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, _host_party_id(user))
    return build_case_export(db, case, viewer_is_admin=False)


@host_router.post("/{case_id}/evidence", response_model=DisputeEvidenceRead, status_code=status.HTTP_201_CREATED)
async def post_upload_evidence_as_host(
    case_id: int, request: Request,
    file: UploadFile | None = File(default=None), note_text: str = Form(default=""), claim_ids: list[int] = Form(default=[]),
    captured_at: datetime | None = Form(default=None),
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    party_id = _host_party_id(user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, party_id)
    evidence = await evidence_crud.upload_evidence(
        db, case, file=file, note_text=note_text, claim_ids=claim_ids, party_id=party_id, captured_at=captured_at,
    )
    log_audit_event(db, None, "dispute_evidence.upload", "dispute_evidence_item", str(evidence.id), get_correlation_id(request), reason=f"party:{party_id}")
    emit_event(
        db, "evidence.received", "dispute_evidence_item", str(evidence.id),
        {"evidenceId": evidence.id, "caseId": case.id, "provenance": evidence.provenance},
        correlation_id=get_correlation_id(request), actor_kind="party", actor_id=str(party_id),
    )
    db.commit()
    return _to_evidence_read(db, evidence)


@host_router.get("/{case_id}/evidence", response_model=list[DisputeEvidenceRead])
def get_evidence_as_host(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, _host_party_id(user))
    items = evidence_crud.list_evidence_for_case(db, case, viewer_is_admin=False)
    return [_to_evidence_read(db, e) for e in items]


@host_router.get("/{case_id}/evidence/{evidence_id}/file")
def get_evidence_file_as_host(
    case_id: int, evidence_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    party_id = _host_party_id(user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, party_id)
    evidence = evidence_crud.get_evidence_or_404(db, evidence_id)
    if evidence.case_id != case_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence item not found on this case")
    evidence_crud.assert_evidence_downloadable(evidence, party_id=party_id)
    return _evidence_file_response(evidence)


@host_router.post("/{case_id}/evidence/{evidence_id}/delete", response_model=DisputeEvidenceRead)
def post_request_evidence_deletion_as_host(
    case_id: int, evidence_id: int, request: Request, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    party_id = _host_party_id(user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, party_id)
    evidence = evidence_crud.get_evidence_or_404(db, evidence_id)
    if evidence.case_id != case_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence item not found on this case")
    updated = evidence_crud.request_deletion(db, evidence, party_id=party_id)
    log_audit_event(
        db, None, "dispute_evidence.delete_requested", "dispute_evidence_item", str(evidence_id), get_correlation_id(request),
        reason=f"party:{party_id}",
    )
    db.commit()
    return _to_evidence_read(db, updated)


@host_router.get("/{case_id}/external-proceedings", response_model=list[DisputeExternalProceedingRead])
def get_external_proceedings_as_host(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, _host_party_id(user))
    return [_to_proceeding_read(db, p) for p in proceeding_crud.list_proceedings_for_case(db, case)]


@host_router.post("/{case_id}/settlements", response_model=DisputeSettlementRead, status_code=status.HTTP_201_CREATED)
def post_propose_settlement_as_host(
    case_id: int, payload: DisputeSettlementCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    party_id = _host_party_id(user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, party_id)
    settlement = settlement_crud.propose_settlement(
        db, case, party_id=party_id, claim_ids=payload.claim_ids, terms_text=payload.terms_text, amount=payload.amount,
        currency=payload.currency, expires_at=payload.expires_at,
        acknowledges_no_nonwaivable_waiver=payload.acknowledges_no_nonwaivable_waiver,
    )
    log_audit_event(db, None, "dispute_settlement.propose", "dispute_settlement", str(settlement.id), get_correlation_id(request), reason=f"party:{party_id}")
    db.commit()
    return _to_settlement_read(db, settlement)


@host_router.get("/{case_id}/settlements", response_model=list[DisputeSettlementRead])
def get_settlements_as_host(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, _host_party_id(user))
    return [_to_settlement_read(db, s) for s in settlement_crud.list_settlements_for_case(db, case)]


@host_router.get("/{case_id}/deadlines", response_model=list[DisputeDeadlineRead])
def get_deadlines_as_host(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, _host_party_id(user))
    return [_to_deadline_read(d) for d in deadline_crud.list_deadlines_for_case(db, case)]


@host_router.post("/{case_id}/messages", response_model=DisputeCaseMessageRead, status_code=status.HTTP_201_CREATED)
def post_message_as_host(
    case_id: int, payload: DisputeCaseMessageCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    party_id = _host_party_id(user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, party_id)
    message = message_crud.post_message(db, case, body=payload.body, party_id=party_id)
    log_audit_event(db, None, "dispute_message.post", "dispute_case_message", str(message.id), get_correlation_id(request), reason=f"party:{party_id}")
    db.commit()
    return _to_message_read(message)


@host_router.get("/{case_id}/messages", response_model=list[DisputeCaseMessageRead])
def get_messages_as_host(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, _host_party_id(user))
    return [_to_message_read(m) for m in message_crud.list_messages_for_case(db, case, viewer_is_admin=False)]


@host_router.get("/{case_id}/parties", response_model=list[DisputePartyRead])
def get_parties_as_host(case_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, _host_party_id(user))
    return [_to_party_read(p) for p in party_crud.list_parties_for_case(db, case)]


@host_router.post("/{case_id}/settlements/{settlement_id}/respond", response_model=DisputeSettlementRead)
def post_respond_settlement_as_host(
    case_id: int, settlement_id: int, payload: DisputeSettlementRespond, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    party_id = _host_party_id(user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, party_id)
    settlement = settlement_crud.get_settlement_or_404(db, settlement_id)
    if settlement.case_id != case_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Settlement not found on this case")
    updated = settlement_crud.respond_settlement(
        db, settlement, case, party_id=party_id, action=payload.action,
        counter_terms_text=payload.counter_terms_text, counter_amount=payload.counter_amount,
        counter_currency=payload.counter_currency, counter_expires_at=payload.counter_expires_at,
    )
    log_audit_event(db, None, "dispute_settlement.respond", "dispute_settlement", str(settlement_id), get_correlation_id(request), reason=payload.action)
    if payload.action == "ACCEPT":
        correlation_id = get_correlation_id(request)
        emit_event(
            db, "settlement.accepted", "dispute_settlement", str(updated.id),
            {"settlementId": updated.id, "caseId": case_id, "amount": float(updated.amount) if updated.amount is not None else None, "currency": updated.currency},
            correlation_id=correlation_id, actor_kind="party", actor_id=str(party_id), new_state=updated.status,
        )
        for linked_claim_id in settlement_crud.claim_ids_for_settlement(db, updated):
            emit_event(
                db, "claim.resolved", "dispute_resolution_claim", str(linked_claim_id),
                {"claimId": linked_claim_id, "caseId": case_id, "outcome": "SETTLED", "settlementId": updated.id},
                correlation_id=correlation_id, actor_kind="party", actor_id=str(party_id), new_state="SETTLED",
            )
    db.commit()
    return _to_settlement_read(db, updated)


@host_router.post("/{case_id}/settlements/{settlement_id}/void", response_model=DisputeSettlementRead)
def post_void_settlement_as_host(
    case_id: int, settlement_id: int, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    party_id = _host_party_id(user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, party_id)
    settlement = settlement_crud.get_settlement_or_404(db, settlement_id)
    if settlement.case_id != case_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Settlement not found on this case")
    updated = settlement_crud.void_settlement(db, settlement, party_id=party_id)
    log_audit_event(db, None, "dispute_settlement.void", "dispute_settlement", str(settlement_id), get_correlation_id(request))
    db.commit()
    return _to_settlement_read(db, updated)


@host_router.post("/{case_id}/claims", response_model=DisputeClaimRead, status_code=status.HTTP_201_CREATED)
def post_add_claim_as_host(
    case_id: int, payload: DisputeClaimCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    party_id = _host_party_id(user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, party_id)
    claim = crud.add_claim(db, case, payload, claimant_role="HOST")
    log_audit_event(db, None, "dispute_claim.add", "dispute_resolution_claim", str(claim.id), get_correlation_id(request), reason=f"party:{party_id}")
    emit_event(
        db, "claim.classified", "dispute_resolution_claim", str(claim.id),
        {"claimId": claim.id, "caseId": case_id, "claimFamily": claim.claim_family, "authorityClass": claim.authority_class},
        correlation_id=get_correlation_id(request), actor_kind="party", actor_id=str(party_id),
    )
    db.commit()
    return claim


@host_router.post("/{case_id}/claims/{claim_id}/request-review", response_model=DisputeClaimRead)
def post_request_review_as_host(
    case_id: int, claim_id: int, payload: DisputeClaimReviewRequest, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    party_id = _host_party_id(user)
    case = crud.get_case_or_404(db, case_id)
    crud.assert_party_can_access_case(case, party_id)
    claim = crud.get_claim_or_404(db, claim_id)
    updated = crud.request_internal_review(db, case, claim, reason=payload.reason)
    log_audit_event(db, None, "dispute_claim.request_review", "dispute_resolution_claim", str(claim_id), get_correlation_id(request), reason=f"party:{party_id}")
    db.commit()
    return updated


# -- Admin dispute-operations console (Section 12) -------------------------

@admin_router.get("", response_model=list[DisputeCaseRead])
def get_cases_as_admin(team: str | None = None, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return crud.list_cases_for_admin(db, admin, team=team)


@admin_router.post("/{case_id}/assign", response_model=DisputeCaseRead)
def post_assign_case(
    case_id: int, payload: DisputeCaseAssign, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "DISPUTE_OFFICER", db=db)
    case = crud.get_case_or_404(db, case_id)
    updated = crud.reassign_case(db, case, admin, team=payload.team, assigned_admin_id=payload.assigned_admin_id)
    log_audit_event(
        db, admin, "dispute_case.assign", "dispute_resolution_case", str(case_id), get_correlation_id(request),
        reason=payload.team or "",
    )
    db.commit()
    return _to_case_read(updated)


@admin_router.get("/{case_id}", response_model=DisputeCaseRead)
def get_case_as_admin(case_id: int, db: Session = Depends(get_db)):
    return _to_case_read(crud.get_case_or_404(db, case_id))


@admin_router.get("/{case_id}/export", response_model=DisputeCaseExportRead)
def get_case_export_as_admin(case_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    return build_case_export(db, case, viewer_is_admin=True, viewer_admin=admin)


@admin_router.get("/{case_id}/decisions", response_model=list[DisputeDecisionRead])
def get_decisions_as_admin(case_id: int, db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    return crud.list_decisions_for_case(db, case)


@admin_router.post("/claims/{claim_id}/decision", response_model=DisputeClaimRead)
def post_decide_claim(
    claim_id: int, payload: DisputeClaimDecide, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "DISPUTE_OFFICER", db=db)
    claim = crud.get_claim_or_404(db, claim_id)
    updated = crud.decide_claim(db, claim, admin, payload)
    log_audit_event(db, admin, "dispute_claim.decide", "dispute_resolution_claim", str(claim_id), get_correlation_id(request), reason=updated.outcome or "")
    emit_event(
        db, "claim.resolved", "dispute_resolution_claim", str(claim_id),
        {"claimId": claim_id, "caseId": updated.case_id, "outcome": updated.outcome, "reasonCode": updated.reason_code},
        correlation_id=get_correlation_id(request), actor_kind="admin", actor_id=str(admin.id), new_state=updated.status,
    )
    db.commit()
    return updated


@admin_router.post("/claims/{claim_id}/financial-holds", response_model=DisputeFinancialHoldRead, status_code=status.HTTP_201_CREATED)
def post_open_financial_hold(
    claim_id: int, payload: DisputeFinancialHoldCreate, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "FINANCE", db=db)
    claim = crud.get_claim_or_404(db, claim_id)
    hold = crud.open_financial_hold(db, claim, admin, payload)
    log_audit_event(db, admin, "dispute_financial_hold.open", "dispute_resolution_hold", str(hold.id), get_correlation_id(request), reason=payload.authority_basis)
    if hold.status == "ACTIVE":
        emit_event(
            db, "hold.activated", "dispute_resolution_hold", str(hold.id),
            {"holdId": hold.id, "claimId": claim_id, "amount": float(hold.amount), "currency": hold.currency},
            correlation_id=get_correlation_id(request), actor_kind="admin", actor_id=str(admin.id), new_state=hold.status,
        )
    db.commit()
    return _to_hold_read(hold)


@admin_router.post("/financial-holds/{hold_id}/approve", response_model=DisputeFinancialHoldRead)
def post_approve_financial_hold(
    hold_id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "FINANCE", db=db)
    hold = crud.get_hold_or_404(db, hold_id)
    updated = crud.approve_financial_hold(db, hold, admin)
    log_audit_event(db, admin, "dispute_financial_hold.approve", "dispute_resolution_hold", str(hold_id), get_correlation_id(request))
    emit_event(
        db, "hold.activated", "dispute_resolution_hold", str(hold_id),
        {"holdId": hold_id, "claimId": updated.claim_id, "amount": float(updated.amount), "currency": updated.currency},
        correlation_id=get_correlation_id(request), actor_kind="admin", actor_id=str(admin.id), new_state=updated.status,
    )
    db.commit()
    return _to_hold_read(updated)


@admin_router.post("/financial-holds/{hold_id}/release", response_model=DisputeFinancialHoldRead)
def post_release_financial_hold(
    hold_id: int, payload: DisputeFinancialHoldRelease, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "FINANCE", db=db)
    hold = crud.get_hold_or_404(db, hold_id)
    updated = crud.release_financial_hold(db, hold, admin, payload)
    log_audit_event(db, admin, "dispute_financial_hold.release", "dispute_resolution_hold", str(hold_id), get_correlation_id(request), reason=payload.release_reason)
    db.commit()
    return _to_hold_read(updated)


@admin_router.post("/financial-holds/{hold_id}/confirm-release", response_model=DisputeFinancialHoldRead)
def post_confirm_release_financial_hold(
    hold_id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "FINANCE", db=db)
    hold = crud.get_hold_or_404(db, hold_id)
    updated = crud.confirm_release_financial_hold(db, hold, admin)
    log_audit_event(db, admin, "dispute_financial_hold.confirm_release", "dispute_resolution_hold", str(hold_id), get_correlation_id(request))
    db.commit()
    return _to_hold_read(updated)


@admin_router.post("/{case_id}/evidence", response_model=DisputeEvidenceRead, status_code=status.HTTP_201_CREATED)
async def post_upload_evidence_as_admin(
    case_id: int, request: Request,
    file: UploadFile | None = File(default=None), note_text: str = Form(default=""), claim_ids: list[int] = Form(default=[]),
    disclosure_class: str | None = Form(default=None), captured_at: datetime | None = Form(default=None),
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    case = crud.get_case_or_404(db, case_id)
    evidence = await evidence_crud.upload_evidence(
        db, case, file=file, note_text=note_text, claim_ids=claim_ids, admin=admin, disclosure_class=disclosure_class,
        captured_at=captured_at,
    )
    log_audit_event(db, admin, "dispute_evidence.upload", "dispute_evidence_item", str(evidence.id), get_correlation_id(request))
    emit_event(
        db, "evidence.received", "dispute_evidence_item", str(evidence.id),
        {"evidenceId": evidence.id, "caseId": case_id, "provenance": evidence.provenance},
        correlation_id=get_correlation_id(request), actor_kind="admin", actor_id=str(admin.id),
    )
    db.commit()
    return _to_evidence_read(db, evidence)


@admin_router.get("/{case_id}/evidence", response_model=list[DisputeEvidenceRead])
def get_evidence_as_admin(case_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    items = evidence_crud.list_evidence_for_case(db, case, viewer_is_admin=True, viewer_admin=admin)
    return [_to_evidence_read(db, e) for e in items]


@admin_router.get("/evidence/{evidence_id}/file")
def get_evidence_file_as_admin(evidence_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    evidence = evidence_crud.get_evidence_or_404(db, evidence_id)
    evidence_crud.assert_evidence_downloadable(evidence, admin=admin)
    return _evidence_file_response(evidence)


@admin_router.post("/evidence/{evidence_id}/redact", response_model=DisputeEvidenceRead, status_code=status.HTTP_201_CREATED)
async def post_redact_evidence(
    evidence_id: int, request: Request, file: UploadFile = File(...),
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    original = evidence_crud.get_evidence_or_404(db, evidence_id)
    redaction = await evidence_crud.create_redaction(db, original, admin, file=file)
    log_audit_event(
        db, admin, "dispute_evidence.redact", "dispute_evidence_item", str(redaction.id), get_correlation_id(request),
        reason=f"redacted_of:{original.id}",
    )
    db.commit()
    return _to_evidence_read(db, redaction)


@admin_router.post("/evidence/{evidence_id}/legal-hold", response_model=DisputeEvidenceRead)
def post_set_legal_hold(
    evidence_id: int, payload: DisputeEvidenceLegalHoldUpdate, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "TRUST_AND_SAFETY", "LEGAL_COMPLIANCE", db=db)
    evidence = evidence_crud.get_evidence_or_404(db, evidence_id)
    updated = evidence_crud.set_legal_hold(db, evidence, admin, payload.hold, reason=payload.reason)
    log_audit_event(
        db, admin, "dispute_evidence.legal_hold", "dispute_evidence_item", str(evidence_id), get_correlation_id(request),
        reason=payload.reason or str(payload.hold),
    )
    db.commit()
    return _to_evidence_read(db, updated)


@admin_router.get("/{case_id}/legal-holds", response_model=list[DisputeLegalHoldRead])
def get_legal_holds_as_admin(case_id: int, db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    return evidence_crud.list_legal_holds_for_case(db, case)


@admin_router.post("/evidence/{evidence_id}/verify", response_model=DisputeEvidenceRead)
def post_verify_evidence(
    evidence_id: int, payload: DisputeEvidenceVerify, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "TRUST_AND_SAFETY", db=db)
    evidence = evidence_crud.get_evidence_or_404(db, evidence_id)
    updated = evidence_crud.verify_evidence(db, evidence, admin, verified=payload.verified)
    log_audit_event(
        db, admin, "dispute_evidence.verify", "dispute_evidence_item", str(evidence_id), get_correlation_id(request),
        reason=str(payload.verified),
    )
    db.commit()
    return _to_evidence_read(db, updated)


@admin_router.post("/evidence/{evidence_id}/archive", response_model=DisputeEvidenceRead)
def post_archive_evidence(
    evidence_id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "TRUST_AND_SAFETY", db=db)
    evidence = evidence_crud.get_evidence_or_404(db, evidence_id)
    updated = evidence_crud.archive_evidence(db, evidence, admin)
    log_audit_event(db, admin, "dispute_evidence.archive", "dispute_evidence_item", str(evidence_id), get_correlation_id(request))
    db.commit()
    return _to_evidence_read(db, updated)


@admin_router.post("/evidence/{evidence_id}/delete", response_model=DisputeEvidenceRead)
def post_request_evidence_deletion_as_admin(
    evidence_id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "TRUST_AND_SAFETY", "LEGAL_COMPLIANCE", db=db)
    evidence = evidence_crud.get_evidence_or_404(db, evidence_id)
    updated = evidence_crud.request_deletion(db, evidence, admin=admin)
    log_audit_event(db, admin, "dispute_evidence.delete_requested", "dispute_evidence_item", str(evidence_id), get_correlation_id(request))
    db.commit()
    return _to_evidence_read(db, updated)


@admin_router.post("/{case_id}/external-proceedings", response_model=DisputeExternalProceedingRead, status_code=status.HTTP_201_CREATED)
def post_file_external_proceeding(
    case_id: int, payload: DisputeExternalProceedingCreate, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "DISPUTE_OFFICER", "LEGAL_COMPLIANCE", db=db)
    case = crud.get_case_or_404(db, case_id)
    proceeding = proceeding_crud.file_proceeding(
        db, case, admin,
        authority_type=payload.authority_type, authority_name=payload.authority_name,
        external_reference=payload.external_reference, claim_ids=payload.claim_ids, filed_at=payload.filed_at,
    )
    log_audit_event(
        db, admin, "dispute_external_proceeding.file", "dispute_external_proceeding", str(proceeding.id), get_correlation_id(request),
        reason=payload.authority_type,
    )
    emit_event(
        db, "claim.referred", "dispute_external_proceeding", str(proceeding.id),
        {"proceedingId": proceeding.id, "caseId": case_id, "authorityType": payload.authority_type, "claimIds": payload.claim_ids},
        correlation_id=get_correlation_id(request), actor_kind="admin", actor_id=str(admin.id), new_state=proceeding.status,
    )
    db.commit()
    return _to_proceeding_read(db, proceeding)


@admin_router.get("/{case_id}/external-proceedings", response_model=list[DisputeExternalProceedingRead])
def get_external_proceedings_as_admin(case_id: int, db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    return [_to_proceeding_read(db, p) for p in proceeding_crud.list_proceedings_for_case(db, case)]


@admin_router.post("/external-proceedings/{proceeding_id}/status", response_model=DisputeExternalProceedingRead)
def post_update_external_proceeding_status(
    proceeding_id: int, payload: DisputeExternalProceedingStatusUpdate, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "DISPUTE_OFFICER", "LEGAL_COMPLIANCE", db=db)
    proceeding = proceeding_crud.get_proceeding_or_404(db, proceeding_id)
    updated = proceeding_crud.update_status(db, proceeding, admin, new_status=payload.status)
    log_audit_event(
        db, admin, "dispute_external_proceeding.status", "dispute_external_proceeding", str(proceeding_id), get_correlation_id(request),
        reason=payload.status,
    )
    db.commit()
    return _to_proceeding_read(db, updated)


@admin_router.post("/external-proceedings/{proceeding_id}/decision", response_model=DisputeExternalProceedingRead)
def post_record_external_proceeding_decision(
    proceeding_id: int, payload: DisputeExternalProceedingDecide, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "DISPUTE_OFFICER", "LEGAL_COMPLIANCE", db=db)
    proceeding = proceeding_crud.get_proceeding_or_404(db, proceeding_id)
    updated = proceeding_crud.record_decision(
        db, proceeding, admin,
        outcome=payload.outcome, decision_date=payload.decision_date, finality_state=payload.finality_state,
        outcome_evidence_id=payload.outcome_evidence_id, reason_code=payload.reason_code,
    )
    log_audit_event(
        db, admin, "dispute_external_proceeding.decide", "dispute_external_proceeding", str(proceeding_id), get_correlation_id(request),
        reason=payload.outcome,
    )
    correlation_id = get_correlation_id(request)
    emit_event(
        db, "external.decision_recorded", "dispute_external_proceeding", str(proceeding_id),
        {"proceedingId": proceeding_id, "caseId": updated.case_id, "outcome": payload.outcome, "finalityState": payload.finality_state},
        correlation_id=correlation_id, actor_kind="admin", actor_id=str(admin.id), new_state=updated.status,
    )
    for linked_claim_id in proceeding_crud.claim_ids_for_proceeding(db, updated):
        emit_event(
            db, "claim.resolved", "dispute_resolution_claim", str(linked_claim_id),
            {"claimId": linked_claim_id, "caseId": updated.case_id, "outcome": payload.outcome, "proceedingId": proceeding_id},
            correlation_id=correlation_id, actor_kind="admin", actor_id=str(admin.id),
        )
    db.commit()
    return _to_proceeding_read(db, updated)


@admin_router.get("/{case_id}/settlements", response_model=list[DisputeSettlementRead])
def get_settlements_as_admin(case_id: int, db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    return [_to_settlement_read(db, s) for s in settlement_crud.list_settlements_for_case(db, case)]


@admin_router.post("/{case_id}/deadlines", response_model=DisputeDeadlineRead, status_code=status.HTTP_201_CREATED)
def post_create_deadline(
    case_id: int, payload: DisputeDeadlineCreate, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    case = crud.get_case_or_404(db, case_id)
    deadline = deadline_crud.create_deadline(
        db, case, deadline_type=payload.deadline_type, due_at=payload.due_at, claim_id=payload.claim_id, admin=admin,
    )
    log_audit_event(db, admin, "dispute_deadline.create", "dispute_deadline", str(deadline.id), get_correlation_id(request), reason=payload.deadline_type)
    db.commit()
    return _to_deadline_read(deadline)


@admin_router.get("/{case_id}/deadlines", response_model=list[DisputeDeadlineRead])
def get_deadlines_as_admin(case_id: int, db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    return [_to_deadline_read(d) for d in deadline_crud.list_deadlines_for_case(db, case)]


@admin_router.post("/deadlines/{deadline_id}/extend", response_model=DisputeDeadlineRead)
def post_extend_deadline(
    deadline_id: int, payload: DisputeDeadlineExtend, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    deadline = deadline_crud.get_deadline_or_404(db, deadline_id)
    updated = deadline_crud.extend_deadline(db, deadline, admin, new_due_at=payload.new_due_at, extension_basis=payload.extension_basis)
    log_audit_event(db, admin, "dispute_deadline.extend", "dispute_deadline", str(deadline_id), get_correlation_id(request), reason=payload.extension_basis)
    db.commit()
    return _to_deadline_read(updated)


@admin_router.post("/deadlines/{deadline_id}/cancel", response_model=DisputeDeadlineRead)
def post_cancel_deadline(
    deadline_id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    deadline = deadline_crud.get_deadline_or_404(db, deadline_id)
    updated = deadline_crud.cancel_deadline(db, deadline)
    log_audit_event(db, admin, "dispute_deadline.cancel", "dispute_deadline", str(deadline_id), get_correlation_id(request))
    db.commit()
    return _to_deadline_read(updated)


@admin_router.post("/{case_id}/messages", response_model=DisputeCaseMessageRead, status_code=status.HTTP_201_CREATED)
def post_message_as_admin(
    case_id: int, payload: DisputeCaseMessageCreate, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    case = crud.get_case_or_404(db, case_id)
    message = message_crud.post_message(db, case, body=payload.body, admin=admin, visibility_class=payload.visibility_class)
    log_audit_event(db, admin, "dispute_message.post", "dispute_case_message", str(message.id), get_correlation_id(request))
    db.commit()
    return _to_message_read(message)


@admin_router.get("/{case_id}/messages", response_model=list[DisputeCaseMessageRead])
def get_messages_as_admin(case_id: int, db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    return [_to_message_read(m) for m in message_crud.list_messages_for_case(db, case, viewer_is_admin=True)]


@admin_router.get("/{case_id}/parties", response_model=list[DisputePartyRead])
def get_parties_as_admin(case_id: int, db: Session = Depends(get_db)):
    case = crud.get_case_or_404(db, case_id)
    return [_to_party_read(p) for p in party_crud.list_parties_for_case(db, case)]


@admin_router.post("/{case_id}/parties/representatives", response_model=DisputePartyRead, status_code=status.HTTP_201_CREATED)
def post_add_representative(
    case_id: int, payload: DisputePartyAddRepresentative, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "TRUST_AND_SAFETY", "DISPUTE_OFFICER", db=db)
    case = crud.get_case_or_404(db, case_id)
    dispute_party = party_crud.add_representative(
        db, case, admin,
        represents=payload.represents, representation_type=payload.representation_type,
        authority_evidence_ref=payload.authority_evidence_ref, guest_id=payload.guest_id, party_id=payload.party_id,
    )
    log_audit_event(
        db, admin, "dispute_party.add_representative", "dispute_party", str(dispute_party.id), get_correlation_id(request),
        reason=payload.authority_evidence_ref,
    )
    db.commit()
    return _to_party_read(dispute_party)


@admin_router.post("/parties/{dispute_party_id}/communication-restrictions", response_model=DisputePartyRead)
def post_set_communication_restrictions(
    dispute_party_id: int, payload: DisputePartyRestrict, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "TRUST_AND_SAFETY", db=db)
    dispute_party = party_crud.get_party_or_404(db, dispute_party_id)
    updated = party_crud.set_communication_restrictions(db, dispute_party, admin, restrictions=payload.restrictions)
    log_audit_event(
        db, admin, "dispute_party.set_communication_restrictions", "dispute_party", str(dispute_party_id), get_correlation_id(request),
        reason=payload.restrictions,
    )
    db.commit()
    return _to_party_read(updated)


@admin_router.post("/messages/{message_id}/moderate", response_model=DisputeCaseMessageRead)
def post_moderate_message(
    message_id: int, payload: DisputeCaseMessageModerate, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "TRUST_AND_SAFETY", db=db)
    message = message_crud.get_message_or_404(db, message_id)
    updated = message_crud.moderate_message(db, message, admin, hidden=payload.hidden)
    log_audit_event(db, admin, "dispute_message.moderate", "dispute_case_message", str(message_id), get_correlation_id(request), reason=str(payload.hidden))
    db.commit()
    return _to_message_read(updated)


@admin_router.post("/{case_id}/close", response_model=DisputeCaseRead)
def post_close_case(
    case_id: int, request: Request, payload: DisputeCaseClose = DisputeCaseClose(),
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "DISPUTE_OFFICER", db=db)
    case = crud.get_case_or_404(db, case_id)
    updated = crud.close_case(db, case, admin, force_close_reason=payload.force_close_reason)
    log_audit_event(
        db, admin, "dispute_case.close", "dispute_resolution_case", str(case_id), get_correlation_id(request),
        reason=payload.force_close_reason,
    )
    emit_event(
        db, "dispute.closed", "dispute_resolution_case", str(case_id),
        {"caseId": case_id, "closedByAdminId": admin.id, "partialClosureReason": updated.partial_closure_reason},
        correlation_id=get_correlation_id(request), actor_kind="admin", actor_id=str(admin.id), new_state=updated.status,
    )
    db.commit()
    return _to_case_read(updated)


@admin_router.post("/{case_id}/reopen", response_model=DisputeCaseRead)
def post_reopen_case(
    case_id: int, payload: DisputeCaseReopen, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    assert_dispute_role(admin, "DISPUTE_OFFICER", db=db)
    case = crud.get_case_or_404(db, case_id)
    updated = crud.reopen_case(db, case, admin, grounds=payload.grounds, note=payload.note, claim_ids=payload.claim_ids)
    log_audit_event(db, admin, "dispute_case.reopen", "dispute_resolution_case", str(case_id), get_correlation_id(request), reason=payload.grounds)
    db.commit()
    return _to_case_read(updated)
