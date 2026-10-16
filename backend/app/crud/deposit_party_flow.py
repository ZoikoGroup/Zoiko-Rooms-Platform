"""Deposit claims settled between host and renter -- no admin.

1. The host claims against the deposit (crud/finance.py:submit_deposit_claim)
   up to deposit.claim_days after the tenancy ends.
2. The renter answers each item (accept / accept part / dispute) by the
   claim's response_due_at; an item left unanswered counts as accepted.
3. A disputed item is settled by the host lowering it (or dropping it), or,
   once the negotiation window (dispute.negotiation_days) is over, it's
   referred to the deposit protection scheme and either party records the
   scheme's decision.
4. Once the claim window has closed and nothing is still open, the deposit
   settles on its own: agreed/decided deductions go to the host, the rest
   goes back to the renter. The host can also settle early, giving up any
   further claims."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.models.finance import DepositClaim, DepositClaimItem, DepositRecord
from app.models.guest import Guest
from app.models.occupancy import Occupancy
from app.models.user_account import UserAccount
from app.services import ledger as ledger_service

_OPEN_RECORD = ("HELD", "PARTIALLY_RELEASED", "FROZEN")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _r2(amount) -> float:
    return round(float(amount or 0), 2)


def deposit_occupancy(record: DepositRecord) -> Occupancy | None:
    obligation = record.obligation
    if obligation.occupancy is not None:
        return obligation.occupancy
    if obligation.agreement is not None:
        from sqlalchemy.orm import object_session

        db = object_session(record)
        if db is not None:
            return db.scalar(select(Occupancy).where(Occupancy.offer_id == obligation.agreement.offer_id))
    return None


def _policy(record: DepositRecord, key: str) -> int:
    from app.services.policy import get_policy

    occupancy = deposit_occupancy(record)
    listing = occupancy.listing if occupancy is not None else (
        record.obligation.agreement.offer.listing if record.obligation.agreement else None
    )
    return int(get_policy(listing.market_release if listing else None, key))


def claim_window_ends_at(record: DepositRecord) -> datetime | None:
    """None while the tenancy is still running (the host can claim any time)."""
    occupancy = deposit_occupancy(record)
    if occupancy is None or occupancy.status != "ENDED" or occupancy.ended_at is None:
        return None
    return _aware(occupancy.ended_at) + timedelta(days=_policy(record, "deposit.claim_days"))


def renter_response_due(record: DepositRecord) -> datetime:
    return _now() + timedelta(days=_policy(record, "deposit.renter_response_days"))


def _guest(db: Session, record: DepositRecord) -> Guest | None:
    from app.crud.finance import deposit_record_guest_id

    guest_id = deposit_record_guest_id(record)
    return db.get(Guest, guest_id) if guest_id else None


def _party_id(record: DepositRecord) -> int:
    from app.crud.finance import _deposit_record_party_id

    return _deposit_record_party_id(record)


def _notify_renter(db: Session, record: DepositRecord, claim_id: int | str, title: str, message: str, kind: str) -> None:
    guest = _guest(db, record)
    if guest is not None:
        notif_crud.notify_user_by_guest(
            db, guest, title=title, message=message, notification_type=kind,
            related_entity_type="deposit_claim", related_entity_id=str(claim_id),
        )


def _notify_host(db: Session, record: DepositRecord, claim_id: int | str, title: str, message: str, kind: str) -> None:
    notif_crud.notify_user_by_party(
        db, _party_id(record), title=title, message=message, notification_type=kind,
        related_entity_type="deposit_claim", related_entity_id=str(claim_id),
    )


def _recompute(record: DepositRecord) -> None:
    from app.crud.finance import _recompute_deposit_record_status

    _recompute_deposit_record_status(record)


def _negotiation_days(record: DepositRecord) -> int:
    return _policy(record, "dispute.negotiation_days")


def _sync_claim_status(db: Session, claim: DepositClaim) -> None:
    """Moves a claim forward once every item has an answer: AGREED when
    nothing is disputed, otherwise DISPUTED with a negotiation window."""
    record = claim.deposit_record
    if any(i.tenant_response == "" for i in claim.items):
        claim.status = "RENTER_RESPONSE_PENDING"
    elif any(i.final_amount is None for i in claim.items):
        if claim.status not in ("DISPUTED", "REFERRED"):
            claim.status = "DISPUTED"
            claim.negotiation_ends_at = _now() + timedelta(days=_negotiation_days(record))
            _notify_host(
                db, record, claim.id, "A deposit claim was disputed",
                (
                    "The renter disputed part of your deposit claim. You can lower or drop the disputed items; if you "
                    f"can't agree by {claim.negotiation_ends_at.date().isoformat()}, it goes to the deposit protection "
                    "scheme and either of you records its decision."
                ),
                "deposit_claim.disputed",
            )
    elif claim.status in ("DISPUTED", "REFERRED"):
        claim.status = "RESOLVED"
        claim.resolved_at = _now()
    else:
        claim.status = "AGREED"
    _recompute(record)


def after_renter_answers(db: Session, claim: DepositClaim) -> None:
    db.flush()
    _sync_claim_status(db, claim)


# --- Host lowers a disputed item --------------------------------------------


def _own_record(db: Session, user: UserAccount, record: DepositRecord) -> None:
    from app.crud.finance import assert_deposit_record_access

    assert_deposit_record_access(db, user, record)


def revise_item(db: Session, item: DepositClaimItem, user: UserAccount, amount: float) -> DepositClaim:
    """The host settles a disputed item by lowering it (0 drops it). Lowering
    only ever favours the renter, so it needs no agreement from them."""
    claim = item.claim
    record = claim.deposit_record
    _own_record(db, user, record)
    if item.final_amount is not None or item.tenant_response != "DISPUTE":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a disputed, unsettled item can be lowered")
    if amount > _r2(item.amount_requested):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can only lower an item, not raise it")
    item.final_amount = _r2(amount)
    db.flush()
    _sync_claim_status(db, claim)
    _notify_renter(
        db, record, claim.id, "Your host lowered a deposit claim item",
        f"Your host lowered the {item.category_code.replace('_', ' ').lower()} item to "
        f"{record.obligation.currency} {item.final_amount:.2f}.",
        "deposit_claim.revised",
    )
    db.commit()
    db.refresh(claim)
    return claim


# --- The deposit scheme's decision, recorded by either party -----------------


def record_scheme_decision(
    db: Session, claim: DepositClaim, *, role: str, user: UserAccount, item_final_amounts: list,
    scheme_reference: str, notes: str = "",
) -> DepositClaim:
    record = claim.deposit_record
    if claim.status not in ("DISPUTED", "REFERRED"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a disputed claim takes a deposit scheme decision")
    open_items = {i.id: i for i in claim.items if i.final_amount is None}
    given = {entry.item_id: entry.final_amount for entry in item_final_amounts}
    unknown = set(given) - set(open_items)
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Item(s) {sorted(unknown)} aren't open on this claim")
    if set(open_items) - set(given):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give the scheme's amount for every disputed item")
    for item_id, amount in given.items():
        item = open_items[item_id]
        if amount < 0 or amount > _r2(item.amount_requested):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Each amount must be between 0 and what was claimed")
        item.final_amount = _r2(amount)
    claim.scheme_reference = scheme_reference.strip()[:200]
    claim.resolution_notes = notes.strip()[:2000]
    claim.resolved_by_user_id = user.id
    db.flush()
    _sync_claim_status(db, claim)
    message = (
        f"The deposit scheme's decision (ref {claim.scheme_reference}) was recorded on deposit claim #{claim.id}. "
        "If it's wrong, record the correct decision in a payment dispute."
    )
    if role == "HOST":
        _notify_renter(db, record, claim.id, "Deposit scheme decision recorded", message, "deposit_claim.scheme_decision")
    else:
        _notify_host(db, record, claim.id, "Deposit scheme decision recorded", message, "deposit_claim.scheme_decision")
    db.commit()
    db.refresh(claim)
    return claim


# --- Settling the deposit ----------------------------------------------------


def _has_open_items(record: DepositRecord) -> bool:
    return any(i.final_amount is None for c in record.claims for i in c.items)


def settle_deposit(db: Session, record: DepositRecord, *, basis: str) -> DepositRecord:
    """Agreed/decided deductions go to the host; the rest goes back to the
    renter. Terminal -- no more claims after this."""
    if record.status not in _OPEN_RECORD:
        raise HTTPException(status.HTTP_409_CONFLICT, "This deposit has already been settled")
    if _has_open_items(record):
        raise HTTPException(status.HTTP_409_CONFLICT, "A claim on this deposit is still open")
    deductions = _r2(sum(_r2(i.final_amount) for c in record.claims for i in c.items))
    remaining = _r2(record.held_amount) - _r2(record.released_amount)
    deductions = min(deductions, max(0.0, remaining))
    to_renter = _r2(max(0.0, remaining - deductions))

    currency = record.obligation.currency
    party_id = _party_id(record)
    custody = ledger_service.get_party_account(db, "DEPOSIT_CUSTODY_LIABILITY", party_id, currency)
    if deductions > 0:
        ledger_service.post_entry(
            db, debit_account=custody, credit_account=ledger_service.get_party_account(db, "HOST_PAYABLE", party_id, currency),
            amount=deductions, currency=currency, description="Deposit deductions to host",
            source_type="deposit_record", source_id=str(record.id),
        )
    if to_renter > 0:
        ledger_service.post_entry(
            db, debit_account=custody, credit_account=ledger_service.get_platform_account(db, "PLATFORM_CLEARING", currency),
            amount=to_renter, currency=currency, description="Deposit released",
            source_type="deposit_record", source_id=str(record.id),
        )

    record.released_amount = _r2(record.released_amount) + to_renter
    record.released_at = _now()
    record.status = "FORFEITED" if to_renter == 0 and deductions > 0 else "RELEASED"
    record.notes = basis
    for claim in record.claims:
        if claim.status == "RENTER_RESPONSE_PENDING":  # can't happen with no open items; defensive
            claim.status = "AGREED"

    _notify_renter(
        db, record, f"settle:{record.id}", "Your deposit was settled",
        f"{currency} {to_renter:.2f} of your deposit is being returned to you"
        + (f"; {currency} {deductions:.2f} was deducted for agreed or decided claims." if deductions else "."),
        "deposit.settled",
    )
    _notify_host(
        db, record, f"settle:{record.id}", "A deposit was settled",
        f"Deposit settled: {currency} {deductions:.2f} in deductions to you, {currency} {to_renter:.2f} back to the renter.",
        "deposit.settled",
    )
    db.commit()
    db.refresh(record)
    return record


def host_settle_now(db: Session, record: DepositRecord, user: UserAccount) -> DepositRecord:
    """The host gives up any further claims and settles straight away."""
    _own_record(db, user, record)
    return settle_deposit(db, record, basis="Settled early by the host")


def apply_deposit_deadlines(db: Session) -> int:
    """Scheduler job; idempotent."""
    now = _now()
    changed = 0
    claims = db.scalars(select(DepositClaim).where(
        DepositClaim.status.in_(("RENTER_RESPONSE_PENDING", "DISPUTED")),
    ).order_by(DepositClaim.id)).all()
    for claim in claims:
        record = claim.deposit_record
        if record.status not in _OPEN_RECORD:
            continue
        if claim.status == "RENTER_RESPONSE_PENDING":
            if claim.response_due_at is None:  # claims from before the deadline existed
                claim.response_due_at = renter_response_due(record)
                db.commit()
                continue
            if _aware(claim.response_due_at) > now:
                continue
            for item in claim.items:
                if item.tenant_response == "":
                    item.tenant_response = "DEEMED_ACCEPTED"
                    item.final_amount = item.amount_requested
            db.flush()
            _sync_claim_status(db, claim)
            _notify_renter(
                db, record, f"{claim.id}:deemed", "Deposit claim accepted",
                f"You didn't answer deposit claim #{claim.id} in time, so the unanswered items count as accepted.",
                "deposit_claim.deemed_accepted",
            )
        else:
            if claim.negotiation_ends_at is None:
                claim.negotiation_ends_at = now + timedelta(days=_negotiation_days(record))
                db.commit()
                continue
            if _aware(claim.negotiation_ends_at) > now:
                continue
            claim.status = "REFERRED"
            claim.referred_at = now
            message = (
                f"No agreement was reached on deposit claim #{claim.id}. Take the disputed items to your deposit "
                "protection scheme, then record its decision on the claim. Until then the disputed amount stays held."
            )
            _notify_renter(db, record, f"{claim.id}:referred", "Deposit dispute goes to the scheme", message, "deposit_claim.referred")
            _notify_host(db, record, f"{claim.id}:referred", "Deposit dispute goes to the scheme", message, "deposit_claim.referred")
        changed += 1
        db.commit()

    for record in db.scalars(select(DepositRecord).where(DepositRecord.status.in_(_OPEN_RECORD)).order_by(DepositRecord.id)).all():
        window = claim_window_ends_at(record)
        if window is None or window > now or _has_open_items(record):
            continue
        settle_deposit(db, record, basis="Claim window closed")
        changed += 1
    return changed
