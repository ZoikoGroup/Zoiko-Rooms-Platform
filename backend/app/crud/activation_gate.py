"""Fail-closed, reusable activation-gate evaluation for pending occupancies."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.crud.eligibility import check_marketplace_standing
from app.crud.identity_verification import get_verified_identity_for_party
from app.models.admin_user import AdminUser
from app.models.finance import DisputeCase
from app.models.identity_verification import IdentityVerification
from app.models.occupancy import Occupancy
from app.models.market_release import MarketRelease
from app.models.occupancy_activation import OccupancyActivationDecision, OccupancyHandoverEvent

GATE_RULE_VERSION = 1


@dataclass(frozen=True)
class GateEvaluation:
    outcome: str
    reason_codes: list[str]
    checks: dict


def handover_events_for(db: Session, occupancy_id: int) -> list[OccupancyHandoverEvent]:
    return list(db.scalars(
        select(OccupancyHandoverEvent)
        .where(OccupancyHandoverEvent.occupancy_id == occupancy_id)
        .order_by(OccupancyHandoverEvent.created_at, OccupancyHandoverEvent.id)
    ))


def activation_decisions_for(db: Session, occupancy_id: int) -> list[OccupancyActivationDecision]:
    return list(db.scalars(
        select(OccupancyActivationDecision)
        .where(OccupancyActivationDecision.occupancy_id == occupancy_id)
        .order_by(OccupancyActivationDecision.decision_version)
    ))


def evaluate_activation_gate(db: Session, occupancy: Occupancy) -> GateEvaluation:
    """Evaluate authoritative state only. Date eligibility deliberately fails closed:
    the current product has no authoritative date/tolerance/timezone rule."""
    agreement = occupancy.offer.agreement
    offer = occupancy.offer
    listing = occupancy.listing
    reasons: list[str] = []
    checks: dict[str, str] = {}
    blocked: list[str] = []
    waiting: list[str] = []
    review: list[str] = []

    if occupancy.status != "PENDING_MOVE_IN":
        blocked.append("OCCUPANCY_NOT_PENDING_MOVE_IN")
        checks["occupancy"] = "FAILED"
    else:
        checks["occupancy"] = "PASSED"

    if not agreement or agreement.status != "SIGNED":
        blocked.append("AGREEMENT_NOT_SIGNED")
        checks["agreement"] = "FAILED"
    else:
        checks["agreement"] = "PASSED"

    if offer.status != "ACCEPTED":
        blocked.append("OFFER_NOT_ACCEPTED")
        checks["offer"] = "FAILED"
    else:
        checks["offer"] = "PASSED"

    market_release = db.get(MarketRelease, listing.market_release_id) if listing.market_release_id else None
    marketplace_reasons = check_marketplace_standing(db, listing.room, market_release)
    if marketplace_reasons:
        blocked.extend("MARKETPLACE_" + reason.upper().replace(" ", "_") for reason in marketplace_reasons)
        checks["marketplace_compliance"] = "FAILED"
    else:
        checks["marketplace_compliance"] = "PASSED"

    if listing.state != "PUBLISHED":
        blocked.append("LISTING_NOT_PUBLISHED")
        checks["listing"] = "FAILED"
    else:
        checks["listing"] = "PASSED"

    obligations = list(agreement.obligations) if agreement else []
    failed = [o for o in obligations if o.status in ("FAILED", "REFUNDED")]
    unpaid = [o for o in obligations if o.status not in ("PAID", "WAIVED", "FAILED", "REFUNDED")]
    if failed:
        blocked.append("REQUIRED_PAYMENT_FAILED")
        checks["payments"] = "FAILED"
    elif unpaid:
        waiting.append("REQUIRED_PAYMENT_PENDING")
        checks["payments"] = "WAITING"
    else:
        checks["payments"] = "PASSED"

    guest_user = occupancy.guest.user_account
    verified = guest_user and guest_user.party_id and get_verified_identity_for_party(db, guest_user.party_id)
    if verified:
        checks["renter_identity"] = "PASSED"
    else:
        latest_identity = None
        if guest_user and guest_user.party_id:
            latest_identity = db.scalar(
                select(IdentityVerification)
                .where(IdentityVerification.party_id == guest_user.party_id)
                .order_by(IdentityVerification.id.desc())
            )
        if latest_identity and latest_identity.status == "rejected":
            blocked.append("RENTER_IDENTITY_REJECTED")
            checks["renter_identity"] = "FAILED"
        else:
            waiting.append("RENTER_IDENTITY_VERIFICATION_REQUIRED")
            checks["renter_identity"] = "WAITING"

    events = handover_events_for(db, occupancy.id)
    event_types = {event.event_type for event in events}
    for event_type, reason in (
        ("HANDOVER_READY", "HANDOVER_READY_REQUIRED"),
        ("POSSESSION_DELIVERED", "POSSESSION_DELIVERED_REQUIRED"),
        ("RENTER_RECEIPT", "RENTER_RECEIPT_REQUIRED"),
    ):
        if event_type not in event_types:
            waiting.append(reason)
            checks[event_type.lower()] = "WAITING"
        else:
            checks[event_type.lower()] = "PASSED"

    open_dispute = db.scalar(
        select(DisputeCase.id).where(DisputeCase.occupancy_id == occupancy.id, DisputeCase.status == "OPEN").limit(1)
    )
    if open_dispute:
        # Categories have no activation semantics in the current policy. Fail closed
        # into review instead of inventing category-specific blocking behavior.
        review.append("OPEN_OCCUPANCY_DISPUTE_REQUIRES_REVIEW")
        checks["occupancy_dispute"] = "MANUAL_REVIEW"
    else:
        checks["occupancy_dispute"] = "PASSED"

    # No authoritative rule exists for activation date, tolerance, or timezone.
    waiting.append("DATE_ELIGIBILITY_UNRESOLVED")
    checks["date_eligibility"] = "UNRESOLVED"

    reasons = blocked + review + waiting
    if blocked:
        outcome = "BLOCKED"
    elif review:
        outcome = "MANUAL_REVIEW"
    elif waiting:
        outcome = "WAITING_FOR_GATE"
    else:
        outcome = "ACTIVATE"
    return GateEvaluation(outcome=outcome, reason_codes=reasons, checks=checks)


def persist_activation_decision(
    db: Session, occupancy: Occupancy, evaluation: GateEvaluation, *, trigger: str,
    admin: AdminUser | None = None, correlation_id: str = "",
) -> OccupancyActivationDecision:
    next_version = (db.scalar(
        select(func.max(OccupancyActivationDecision.decision_version))
        .where(OccupancyActivationDecision.occupancy_id == occupancy.id)
    ) or 0) + 1
    decision = OccupancyActivationDecision(
        occupancy_id=occupancy.id,
        decision_version=next_version,
        gate_rule_version=GATE_RULE_VERSION,
        outcome=evaluation.outcome,
        reason_codes=evaluation.reason_codes,
        checks=evaluation.checks,
        trigger=trigger,
        evaluating_admin_id=admin.id if admin else None,
        correlation_id=correlation_id,
    )
    db.add(decision)
    db.flush()
    return decision
