"""ZR-SUBLET-PAY-003 -- sublet payments, paid directly by bank transfer, UPI or
cash (no card / Stripe rail; Zoiko Rooms never receives, holds or forwards
sublet rent or deposits and charges no fee on them -- Listing Fee only).

The verified payee is never a free-form choice. It is derived from
approved sublet authority -> agreement/arrangement -> jurisdiction rules ->
verified payment instructions (Section 2 "Payee selection rule"); when those
conflict the payment is blocked and the case is Action Required. Deposits
are routed independently (Section 9). Every check is re-run server-side
whenever a payment is reported (Section 4 server-side invariant).
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.events import emit_event
from app.models.rental_payment import RentalPaymentObligation
from app.models.sublet_payment import DEPOSIT_ROUTES, DIRECT_PAYMENT_METHODS, RENT_PAYEE_TYPES, SubletPaymentArrangement
from app.models.sublet_request import SubletRequest
from app.models.user_account import UserAccount

RESOURCE = "sublet_payment_arrangement"
# Arrangement types whose approval creates a separate agreement the incoming
# tenant pays under (an assignment takes over the existing tenancy instead).
ARRANGEMENT_TYPES_WITH_OWN_PAYMENTS = ("SUBLEASE_PARTIAL", "LODGER_OR_LICENSEE", "ADD_CO_TENANT")

# Section 9: the Country Regulatory Pack's deposit route. Versioned; no
# country is hard-coded in business logic outside this table.
DEPOSIT_PACK_VERSION = "2026.10"
DEPOSIT_PACK = {
    "GB": {"route": "PAY_TO_CUSTODIAN", "sublessor_allowed": False,
           "custodian_hint": "A government-approved tenancy deposit protection scheme (DPS, mydeposits or TDS), "
                             "within 30 days."},
    "IN": {"route": "PAY_TO_LANDLORD_OR_AGENT", "sublessor_allowed": True, "custodian_hint": ""},
    "US": {"route": "PAY_TO_LANDLORD_OR_AGENT", "sublessor_allowed": True, "custodian_hint": ""},
    "*": {"route": "PAY_TO_LANDLORD_OR_AGENT", "sublessor_allowed": True, "custodian_hint": ""},
}

PAYEE_BASIS_TEXT = {
    "LANDLORD_LISTING_AUTHORITY": "Rent is paid to the landlord or their authorized agent, who holds verified authority for this property.",
    "PROPERTY_OWNER": "Rent is paid to the property's owner.",
    "AUTHORITY_VERIFIED_COLLECT_RENT": "The landlord confirmed the tenant may collect rent from the sublet (verified sublet authority).",
    "SUBLET_APPROVAL_PERMITS_COLLECTION": "The approved sublet lets the original tenant collect the subtenant's rent.",
    "CO_TENANT_PAYS_LANDLORD": "A co-tenant pays the landlord directly under their own agreement.",
    "DEPOSIT_PROTECTION_SCHEME": "The deposit must be protected with an approved deposit scheme.",
}
REASON_TEXT = {
    "SUBLET_NOT_ACTIVE": "This sublet is no longer approved, so payments are paused until it's reviewed.",
    "PAYEE_NOT_AUTHORIZED": "The selected payee isn't authorized to collect this payment. The arrangement must be corrected.",
    "PAYEE_MISSING": "We couldn't identify who should receive this payment.",
    "PAYMENT_SETUP_INCOMPLETE": "Payment setup incomplete -- the payee hasn't added confirmed bank or UPI details yet.",
    "DEPOSIT_ROUTE_UNRESOLVED": "Where the deposit must be paid isn't settled yet, so the deposit can't be paid.",
    "CUSTODIAN_DETAILS_MISSING": "The deposit must go to a deposit protection scheme, but the scheme's details haven't been added.",
    "DEPOSIT_TO_SUBLESSOR_NOT_PERMITTED": "In this country the deposit can't be paid to the original tenant.",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# -- lookups -------------------------------------------------------------------------

def sublet_for_obligation(db: Session, obligation: RentalPaymentObligation) -> SubletRequest | None:
    from app.crud.rental_payment import _agreement_id_for_obligation, sublet_for_agreement

    sublet = sublet_for_agreement(db, _agreement_id_for_obligation(obligation))
    return sublet if sublet is not None and sublet.arrangement_type in ARRANGEMENT_TYPES_WITH_OWN_PAYMENTS else None


def current_arrangement(db: Session, sublet_id: int) -> SubletPaymentArrangement | None:
    return db.scalar(select(SubletPaymentArrangement).where(
        SubletPaymentArrangement.sublet_request_id == sublet_id, SubletPaymentArrangement.status == "ACTIVE",
    ).order_by(SubletPaymentArrangement.id.desc()))


def _room(sublet: SubletRequest):
    return sublet.current_occupancy.room if sublet.current_occupancy else None


def country_for(sublet: SubletRequest) -> str:
    from app.services.authority_service import country_for as property_country

    room = _room(sublet)
    return property_country(room.property) if room is not None and room.property is not None else ""


def landlord_payee(db: Session, sublet: SubletRequest) -> tuple[int | None, str, str]:
    """(party_id, basis, authority_ref) for the landlord / authorized agent."""
    from app.crud.authority import get_valid_authority_for_room
    from app.models.authority_verification import AuthorityVerification

    room = _room(sublet)
    if room is None:
        return None, "", ""
    authority = get_valid_authority_for_room(db, room.id)
    if authority is not None:
        kind = "authority_verification" if isinstance(authority, AuthorityVerification) else "authority_record"
        return authority.party_id, "LANDLORD_LISTING_AUTHORITY", f"{kind}:{authority.id}"
    return room.property.owner_party_id, "PROPERTY_OWNER", f"property:{room.property_id}"


def sublessor_party_id(db: Session, sublet: SubletRequest) -> int | None:
    from app.crud.rental_payment import original_renter_party_id

    return original_renter_party_id(db, sublet.current_occupancy)


def sublessor_collection_basis(db: Session, sublet: SubletRequest) -> tuple[str, str] | None:
    """Section 19 "Authority": the Tenant/Sublessor may receive rent only when
    verified authority or the approved arrangement permits collection --
    never inferred from the right to sublet alone."""
    from app.crud.rental_payment import SUBLET_ARRANGEMENTS_PAID_TO_ORIGINAL_RENTER
    from app.models.authority_verification import AuthorityVerification

    if sublet.status != "approved" or sublet.arrangement_type not in SUBLET_ARRANGEMENTS_PAID_TO_ORIGINAL_RENTER:
        return None
    party_id = sublessor_party_id(db, sublet)
    room = _room(sublet)
    if party_id is None or room is None:
        return None
    now = _now()
    for v in db.scalars(select(AuthorityVerification).where(
            AuthorityVerification.property_id == room.property_id, AuthorityVerification.party_id == party_id,
            AuthorityVerification.relationship_type == "TENANT_SUBLETTER", AuthorityVerification.state == "VERIFIED")):
        expires = v.expires_at if v.expires_at is None or v.expires_at.tzinfo else v.expires_at.replace(tzinfo=timezone.utc)
        if (expires is None or expires > now) and "COLLECT_RENT" in (v.scope_codes or []) and (
                not v.room_scope_ids or room.id in v.room_scope_ids):
            return "AUTHORITY_VERIFIED_COLLECT_RENT", f"authority_verification:{v.id}"
    if sublet.payee_model == "ORIGINAL_RENTER_PAYEE" or (sublet.policy_snapshot or {}).get("sublessor_may_collect_rent"):
        return "SUBLET_APPROVAL_PERMITS_COLLECTION", f"sublet_request:{sublet.id}"
    return None


def eligible_rent_payees(db: Session, sublet: SubletRequest) -> list[dict]:
    out = []
    landlord_id, basis, ref = landlord_payee(db, sublet)
    if landlord_id is not None:
        if sublet.arrangement_type == "ADD_CO_TENANT":
            basis = "CO_TENANT_PAYS_LANDLORD"
        out.append({"type": "LANDLORD_AGENT", "party_id": landlord_id, "basis": basis, "ref": ref})
    sub_basis = sublessor_collection_basis(db, sublet)
    if sub_basis is not None:
        out.append({"type": "SUBLESSOR", "party_id": sublessor_party_id(db, sublet),
                    "basis": sub_basis[0], "ref": sub_basis[1]})
    return out


def _party_name(db: Session, party_id: int | None) -> str:
    if not party_id:
        return ""
    user = db.scalar(select(UserAccount).where(UserAccount.party_id == party_id))
    if user is not None and user.full_name:
        return user.full_name
    return f"Party #{party_id}"


# UK deposit protection differs by nation (property jurisdiction_code).
UK_DEPOSIT_SCHEMES = {
    "GB-SCT": "An approved Scottish tenancy deposit scheme (SafeDeposits Scotland, Letting Protection Service "
              "Scotland or MyDeposits Scotland), within 30 working days.",
    "GB-NIR": "An approved Northern Ireland tenancy deposit scheme (TDS Northern Ireland or MyDeposits Northern "
              "Ireland), within 28 days.",
    "GB-WLS": "A government-approved tenancy deposit protection scheme (DPS, mydeposits or TDS), within 30 days.",
}


def custodian_hint(sublet: SubletRequest) -> str:
    room = _room(sublet)
    code = room.property.jurisdiction_code if room is not None and room.property is not None else ""
    return UK_DEPOSIT_SCHEMES.get(code, deposit_pack(country_for(sublet)).get("custodian_hint", ""))


def deposit_pack(country: str) -> dict:
    return DEPOSIT_PACK.get((country or "").upper(), DEPOSIT_PACK["*"])


# -- arrangement lifecycle ----------------------------------------------------------------

def ensure_arrangement(db: Session, sublet: SubletRequest, *, rent_amount: float, deposit_amount: float,
                       currency: str, first_payment_date: date | None) -> SubletPaymentArrangement | None:
    """Created when the sublet is approved -- the payee follows the approved
    arrangement; the deposit route comes from the country pack."""
    if sublet.arrangement_type not in ARRANGEMENT_TYPES_WITH_OWN_PAYMENTS:
        return None
    existing = current_arrangement(db, sublet.id)
    if existing is not None:
        return existing
    payees = {p["type"]: p for p in eligible_rent_payees(db, sublet)}
    wants_sublessor = sublet.payee_model == "ORIGINAL_RENTER_PAYEE" and "SUBLESSOR" in payees
    payee = payees.get("SUBLESSOR") if wants_sublessor else payees.get("LANDLORD_AGENT")
    pack = deposit_pack(country_for(sublet))
    route = "NOT_REQUIRED" if not deposit_amount else pack["route"]
    arrangement = SubletPaymentArrangement(
        sublet_request_id=sublet.id, rent_payee_type=payee["type"] if payee else "LANDLORD_AGENT",
        rent_payee_party_id=payee["party_id"] if payee else None, rent_payee_basis=payee["basis"] if payee else "",
        payee_authority_ref=payee["ref"] if payee else "", deposit_route=route,
        deposit_payee_party_id=(payees.get("LANDLORD_AGENT") or {}).get("party_id") if route != "PAY_TO_CUSTODIAN" else None,
        accepted_methods=_default_methods(db, payee["party_id"] if payee else None),
        rent_amount=rent_amount, deposit_amount=deposit_amount or 0, currency=currency,
        first_payment_date=first_payment_date, rent_due_day=first_payment_date.day if first_payment_date else None,
        deposit_pack_version=DEPOSIT_PACK_VERSION,
    )
    db.add(arrangement)
    db.flush()
    arrangement.reason_codes = evaluate(db, arrangement)["reason_codes"]
    emit_event(db, "SUBLET_PAYMENT_ARRANGEMENT_CREATED", RESOURCE, str(arrangement.id),
               {"subletRequestId": sublet.id, "rentPayeeType": arrangement.rent_payee_type,
                "depositRoute": arrangement.deposit_route})
    return arrangement


def _default_methods(db: Session, party_id: int | None) -> list[str]:
    from app.crud.rental_payment import get_active_rental_payment_instruction

    instruction = get_active_rental_payment_instruction(db, party_id) if party_id else None
    if instruction is not None and instruction.method in DIRECT_PAYMENT_METHODS:
        return [instruction.method] if instruction.method == "CASH" else [instruction.method, "CASH"]
    return ["BANK_TRANSFER", "UPI", "CASH"]


def _payee_readiness(db: Session, party_id: int | None, methods: list[str]) -> str | None:
    """None when the payee can be paid by one of `methods`, else a reason code."""
    from app.crud.rental_payment import get_active_rental_payment_instruction

    if not party_id:
        return "PAYEE_MISSING"
    if "CASH" in methods:
        return None
    instruction = get_active_rental_payment_instruction(db, party_id)
    if instruction is None or instruction.method not in methods:
        return "PAYMENT_SETUP_INCOMPLETE"
    return None


def evaluate(db: Session, a: SubletPaymentArrangement) -> dict:
    """Section 4 preconditions, re-run on every read and before every
    payment report. -> {"rent": {...}, "deposit": {...}, "reason_codes": [...]}"""
    sublet = db.get(SubletRequest, a.sublet_request_id)
    methods = list(a.accepted_methods or [])
    rent_codes: list[str] = []
    if sublet.status != "approved":
        rent_codes.append("SUBLET_NOT_ACTIVE")
    eligible = {p["type"]: p for p in eligible_rent_payees(db, sublet)}
    if a.rent_payee_type not in eligible or eligible[a.rent_payee_type]["party_id"] != a.rent_payee_party_id:
        rent_codes.append("PAYEE_NOT_AUTHORIZED")
    readiness = _payee_readiness(db, a.rent_payee_party_id, methods)
    if readiness:
        rent_codes.append(readiness)

    deposit_codes: list[str] = []
    pack = deposit_pack(country_for(sublet))
    if a.deposit_route == "NOT_REQUIRED" or not float(a.deposit_amount or 0):
        pass
    elif a.deposit_route == "PROHIBITED_OR_UNRESOLVED":
        deposit_codes.append("DEPOSIT_ROUTE_UNRESOLVED")
    elif a.deposit_route == "PAY_TO_CUSTODIAN":
        if not (a.custodian_name.strip() and a.custodian_instructions.strip()):
            deposit_codes.append("CUSTODIAN_DETAILS_MISSING")
    elif a.deposit_route == "PAY_TO_SUBLESSOR":
        if not pack["sublessor_allowed"] or "SUBLESSOR" not in eligible:
            deposit_codes.append("DEPOSIT_TO_SUBLESSOR_NOT_PERMITTED")
        else:
            code = _payee_readiness(db, a.deposit_payee_party_id, methods)
            if code:
                deposit_codes.append(code)
    else:  # PAY_TO_LANDLORD_OR_AGENT
        code = _payee_readiness(db, a.deposit_payee_party_id, methods)
        if code:
            deposit_codes.append(code)
    if "SUBLET_NOT_ACTIVE" in rent_codes:
        deposit_codes.insert(0, "SUBLET_NOT_ACTIVE")

    def state(codes):
        if not codes:
            return "READY"
        if "SUBLET_NOT_ACTIVE" in codes:
            return "BLOCKED"
        if codes == ["PAYMENT_SETUP_INCOMPLETE"]:
            return "SETUP_INCOMPLETE"
        return "ACTION_REQUIRED"

    return {
        "rent": {"state": state(rent_codes), "reason_codes": rent_codes,
                 "message": REASON_TEXT.get(rent_codes[0], "") if rent_codes else ""},
        "deposit": {"state": "NOT_REQUIRED" if a.deposit_route == "NOT_REQUIRED" else state(deposit_codes),
                    "reason_codes": deposit_codes,
                    "message": REASON_TEXT.get(deposit_codes[0], "") if deposit_codes else ""},
        "reason_codes": list(dict.fromkeys(rent_codes + deposit_codes)),
    }


def is_locked(db: Session, a: SubletPaymentArrangement) -> bool:
    """Locked once the agreement is in force or any payment was confirmed --
    after that a change is an amendment (new version)."""
    if a.locked_at is not None:
        return True
    sublet = db.get(SubletRequest, a.sublet_request_id)
    agreement = sublet.new_agreement
    confirmed = db.scalar(select(RentalPaymentObligation.id).where(
        RentalPaymentObligation.arrangement_id == a.id,
        RentalPaymentObligation.status.in_(("CONFIRMED", "PARTIALLY_PAID")))) is not None
    if (agreement is not None and agreement.status in ("SIGNED", "ACTIVE")) or confirmed:
        a.locked_at = _now()
        db.flush()
        return True
    return False


def update_arrangement(db: Session, user: UserAccount, sublet: SubletRequest, changes: dict, *,
                       expected_version: int | None, amendment_reason: str = "") -> SubletPaymentArrangement:
    """Section 13 POST /sublets/{id}/payment-arrangement. The sublessor or the
    landlord may propose; the payee/deposit options are policy-driven -- an
    ineligible recipient can't be chosen."""
    role = viewer_role(db, user, sublet)
    if role not in ("SUBLESSOR", "LANDLORD"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the original tenant or the landlord can change the payment arrangement")
    current = current_arrangement(db, sublet.id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This sublet has no payment arrangement")
    if expected_version is not None and expected_version != current.version:
        raise HTTPException(status.HTTP_409_CONFLICT, "The arrangement changed in another window. Reload to see the latest version.")
    if sublet.status != "approved":
        raise HTTPException(status.HTTP_409_CONFLICT, REASON_TEXT["SUBLET_NOT_ACTIVE"])
    locked = is_locked(db, current)
    if locked and not amendment_reason.strip():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "The agreement is in force, so changes are an amendment -- give a reason for the amendment")

    eligible = {p["type"]: p for p in eligible_rent_payees(db, sublet)}
    target = current
    if locked:
        target = SubletPaymentArrangement(**{c.name: getattr(current, c.name) for c in current.__table__.columns
                                             if c.name not in ("id", "created_at", "updated_at", "locked_at",
                                                               "previous_id", "version_no", "version", "status")})
        target.previous_id, target.version_no, target.version = current.id, current.version_no + 1, 1
        target.amendment_reason = amendment_reason.strip()[:1000]
        target.accepted_methods = list(current.accepted_methods or [])
        current.status = "SUPERSEDED"
        db.add(target)

    if "rent_payee_type" in changes:
        kind = changes["rent_payee_type"]
        if kind not in RENT_PAYEE_TYPES or kind not in eligible:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That payee isn't authorized to collect this sublet's rent")
        p = eligible[kind]
        target.rent_payee_type, target.rent_payee_party_id = kind, p["party_id"]
        target.rent_payee_basis, target.payee_authority_ref = p["basis"], p["ref"]
        # Keep the receipt-authority check (crud/rental_payment.py) in step.
        sublet.payee_model = "ORIGINAL_RENTER_PAYEE" if kind == "SUBLESSOR" else "HOST_OR_LANDLORD_PAYEE"
    if "accepted_methods" in changes:
        methods = [m for m in dict.fromkeys(changes["accepted_methods"] or []) if m in DIRECT_PAYMENT_METHODS]
        if not methods:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose at least one of bank transfer, UPI or cash")
        target.accepted_methods = methods
    if "deposit_route" in changes:
        route = changes["deposit_route"]
        if route not in DEPOSIT_ROUTES or route == "PROHIBITED_OR_UNRESOLVED":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose where the deposit is paid")
        pack = deposit_pack(country_for(sublet))
        if route == "PAY_TO_SUBLESSOR" and (not pack["sublessor_allowed"] or "SUBLESSOR" not in eligible):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, REASON_TEXT["DEPOSIT_TO_SUBLESSOR_NOT_PERMITTED"])
        if route == "NOT_REQUIRED" and float(target.deposit_amount or 0) > 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The agreement has a deposit, so it must be routed somewhere")
        if pack["route"] == "PAY_TO_CUSTODIAN" and route not in ("PAY_TO_CUSTODIAN", "NOT_REQUIRED"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "In this country the deposit must go to a deposit protection scheme")
        target.deposit_route = route
        target.deposit_payee_party_id = (
            (eligible.get("SUBLESSOR") or {}).get("party_id") if route == "PAY_TO_SUBLESSOR"
            else (eligible.get("LANDLORD_AGENT") or {}).get("party_id") if route == "PAY_TO_LANDLORD_OR_AGENT" else None)
    for field in ("custodian_name", "custodian_reference", "custodian_instructions"):
        if field in changes:
            setattr(target, field, (changes[field] or "").strip()[:2000 if field == "custodian_instructions" else 200])
    target.created_by_user_id = user.id
    if target is current:
        target.version += 1
    target.updated_at = _now()
    db.flush()
    target.reason_codes = evaluate(db, target)["reason_codes"]
    apply_to_open_obligations(db, sublet, target)
    emit_event(db, "SUBLET_PAYMENT_ARRANGEMENT_AMENDED" if locked else "SUBLET_PAYMENT_ARRANGEMENT_UPDATED",
               RESOURCE, str(target.id), {"subletRequestId": sublet.id, "rentPayeeType": target.rent_payee_type,
                                          "depositRoute": target.deposit_route, "versionNo": target.version_no},
               actor_kind="user", actor_id=str(user.id))
    db.commit()
    notify_parties(db, sublet, "Sublet payment arrangement updated",
                   "The payment arrangement for this sublet changed. Check who receives each payment before paying.",
                   exclude_user_id=user.id)
    _notify_setup_incomplete(db, target)
    db.refresh(target)
    return target


# -- obligations -----------------------------------------------------------------------------

def payment_reference(sublet_id: int, obligation: RentalPaymentObligation) -> str:
    suffix = "DEP" if obligation.obligation_type == "DEPOSIT" else obligation.due_date.strftime("%Y%m")
    return f"ZR-SUB{sublet_id}-{suffix}"


def _month_after(d: date) -> date:
    year, month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def apply_to_obligation(db: Session, obligation: RentalPaymentObligation, a: SubletPaymentArrangement) -> None:
    """Stamps the ledger fields (Section 11.1) and the arrangement's payee on
    an obligation that has no payment activity yet. Never reroutes money
    already reported or confirmed."""
    obligation.arrangement_id, obligation.arrangement_version = a.id, a.version_no
    obligation.payment_reference = payment_reference(a.sublet_request_id, obligation)
    obligation.platform_fee_amount = 0
    sublet = db.get(SubletRequest, a.sublet_request_id)
    agreement = sublet.new_agreement
    if agreement is not None:
        obligation.agreement_version_no = max((v.version_no for v in agreement.versions), default=None)
    if obligation.obligation_type == "RENT":
        obligation.period_start = obligation.due_date
        obligation.period_end = _month_after(obligation.due_date) - timedelta(days=1)
    has_activity = bool(obligation.records)
    if obligation.obligation_type == "DEPOSIT":
        payee_type = {"PAY_TO_SUBLESSOR": "SUBLESSOR", "PAY_TO_CUSTODIAN": "CUSTODIAN"}.get(a.deposit_route, "LANDLORD_AGENT")
        party_id = a.deposit_payee_party_id
        if a.deposit_route == "PAY_TO_CUSTODIAN":
            # The scheme isn't a Zoiko party: the landlord confirms the
            # protected deposit was received by the scheme.
            party_id = landlord_payee(db, sublet)[0]
        basis = {"SUBLESSOR": a.rent_payee_basis if a.rent_payee_type == "SUBLESSOR" else "",
                 "CUSTODIAN": "DEPOSIT_PROTECTION_SCHEME"}.get(payee_type, "LANDLORD_LISTING_AUTHORITY")
        ref = a.payee_authority_ref if payee_type == "SUBLESSOR" else ""
    else:
        payee_type, party_id, basis, ref = a.rent_payee_type, a.rent_payee_party_id, a.rent_payee_basis, a.payee_authority_ref
    obligation.payee_type, obligation.payee_basis, obligation.payee_authority_ref = payee_type, basis, ref
    if party_id and not has_activity and obligation.recipient_party_id != party_id:
        obligation.recipient_party_id = party_id
    obligation.version = (obligation.version or 1) + 1


def obligations_for_sublet(db: Session, sublet: SubletRequest) -> list[RentalPaymentObligation]:
    from sqlalchemy import or_

    from app.models.occupancy import Occupancy

    if sublet.new_agreement_id is None:
        return []
    agreement = sublet.new_agreement
    occupancy_ids = list(db.scalars(select(Occupancy.id).where(Occupancy.offer_id == agreement.offer_id))) \
        if agreement is not None else []
    scopes = [RentalPaymentObligation.agreement_id == sublet.new_agreement_id]
    if occupancy_ids:
        scopes.append(RentalPaymentObligation.occupancy_id.in_(occupancy_ids))
    return list(db.scalars(select(RentalPaymentObligation).where(or_(*scopes)).order_by(
        RentalPaymentObligation.due_date, RentalPaymentObligation.id)))


def apply_to_open_obligations(db: Session, sublet: SubletRequest, a: SubletPaymentArrangement) -> None:
    for obligation in obligations_for_sublet(db, sublet):
        if obligation.status in ("CANCELLED", "WAIVED"):
            continue
        apply_to_obligation(db, obligation, a)


def on_obligation_created(db: Session, obligation: RentalPaymentObligation) -> None:
    """Hook from crud/rental_payment.py:create_obligation -- later monthly
    rent for a sublet follows the current arrangement."""
    sublet = sublet_for_obligation(db, obligation)
    if sublet is None:
        return
    a = current_arrangement(db, sublet.id)
    if a is not None:
        apply_to_obligation(db, obligation, a)


# -- the payment gate ----------------------------------------------------------------------------

def payment_gate(db: Session, obligation: RentalPaymentObligation) -> None:
    """Raises 409 when this obligation can't be paid right now (Section 4)."""
    sublet = sublet_for_obligation(db, obligation)
    if sublet is None:
        return
    a = current_arrangement(db, sublet.id)
    if a is None:
        return
    result = evaluate(db, a)
    part = result["deposit"] if obligation.obligation_type == "DEPOSIT" else result["rent"]
    if part["state"] not in ("READY", "NOT_REQUIRED"):
        raise HTTPException(status.HTTP_409_CONFLICT, part["message"] or "Payment to this payee is paused")


# -- views ----------------------------------------------------------------------------------------

def viewer_role(db: Session, user: UserAccount, sublet: SubletRequest) -> str | None:
    """SUBLESSOR (original tenant) / SUBTENANT (incoming) / LANDLORD (landlord
    or agent, or the property owner) -- or None for anyone else."""
    if not user.party_id:
        return None
    if user.party_id == sublessor_party_id(db, sublet):
        return "SUBLESSOR"
    if sublet.proposed_renter_party_id and user.party_id == sublet.proposed_renter_party_id:
        return "SUBTENANT"
    landlord_id, _basis, _ref = landlord_payee(db, sublet)
    room = _room(sublet)
    if user.party_id == landlord_id or (room is not None and room.property.owner_party_id == user.party_id):
        return "LANDLORD"
    return None


def get_sublet_for_viewer(db: Session, user: UserAccount, sublet_id: int) -> tuple[SubletRequest, str]:
    sublet = db.get(SubletRequest, sublet_id)
    if sublet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sublet not found")
    role = viewer_role(db, user, sublet)
    if role is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You aren't part of this sublet")
    return sublet, role


def arrangement_view(db: Session, sublet: SubletRequest, role: str) -> dict:
    a = current_arrangement(db, sublet.id)
    if a is None:
        return {"subletRequestId": sublet.id, "arrangement": None, "role": role, "subletStatus": sublet.status,
                "arrangementType": sublet.arrangement_type, "eligiblePayees": [], "canEdit": False}
    locked = is_locked(db, a)
    db.commit()
    result = evaluate(db, a)
    pack = deposit_pack(country_for(sublet))
    eligible = eligible_rent_payees(db, sublet) if role in ("SUBLESSOR", "LANDLORD") and sublet.status == "approved" else []
    return {
        "subletRequestId": sublet.id, "role": role, "subletStatus": sublet.status,
        "arrangementType": sublet.arrangement_type,
        "arrangement": {
            "id": a.id, "versionNo": a.version_no, "version": a.version, "locked": locked, "lockedAt": a.locked_at,
            "rentPayee": {"type": a.rent_payee_type, "name": _party_name(db, a.rent_payee_party_id),
                          "basis": a.rent_payee_basis, "why": PAYEE_BASIS_TEXT.get(a.rent_payee_basis, "")},
            "deposit": {"route": a.deposit_route, "payeeName": _party_name(db, a.deposit_payee_party_id),
                        "custodianName": a.custodian_name, "custodianReference": a.custodian_reference,
                        "custodianInstructions": a.custodian_instructions,
                        "custodianHint": custodian_hint(sublet), "sublessorAllowed": pack["sublessor_allowed"],
                        "packRoute": pack["route"]},
            "acceptedMethods": list(a.accepted_methods or []),
            "terms": {"rentAmount": float(a.rent_amount or 0), "depositAmount": float(a.deposit_amount or 0),
                      "currency": a.currency, "rentDueDay": a.rent_due_day, "firstPaymentDate": a.first_payment_date,
                      "platformFee": 0.0},
            "amendmentReason": a.amendment_reason, "readiness": result,
        },
        "eligiblePayees": [{"type": p["type"], "name": _party_name(db, p["party_id"]), "basis": p["basis"],
                            "why": PAYEE_BASIS_TEXT.get(p["basis"], "")} for p in eligible],
        "canEdit": role in ("SUBLESSOR", "LANDLORD") and sublet.status == "approved",
    }


EVIDENCE_LABEL = {
    "TENANT_DECLARATION": "Reported by payer -- not independently verified",
    "RECIPIENT_CONFIRMATION": "Confirmed by recipient",
    "PROVIDER_CONFIRMATION": "Confirmed from a bank statement",
    "ADMIN_CORRECTION": "Administrative adjustment",
    "SYSTEM_DERIVATION": "System record",
}


def transactions_view(db: Session, sublet: SubletRequest, role: str) -> dict:
    """Section 13 GET /sublets/{id}/transactions -- one timeline, minimized
    per role (the landlord sees amounts and status, not the payer's payment
    references on payments they don't receive)."""
    from app.models.rental_payment_return import RentalPaymentReturn

    items = []
    obligations = obligations_for_sublet(db, sublet)
    landlord_id = landlord_payee(db, sublet)[0]
    for o in obligations:
        landlord_is_payee = o.recipient_party_id == landlord_id
        items.append({"kind": "OBLIGATION", "at": o.created_at, "obligationId": o.id, "purpose": o.obligation_type,
                      "amount": float(o.amount), "currency": o.currency, "dueDate": o.due_date, "status": o.status,
                      "payeeType": o.payee_type, "payeeName": _party_name(db, o.recipient_party_id),
                      "reference": o.payment_reference if role != "LANDLORD" or landlord_is_payee else "",
                      "evidence": "Payment due (scheduled)"})
        for r in o.records:
            entry = {"kind": "PAYMENT", "at": r.created_at, "obligationId": o.id, "recordId": r.id,
                     "purpose": o.obligation_type, "amount": float(r.confirmed_amount or r.declared_amount),
                     "currency": r.declared_currency, "status": r.status, "method": r.payment_method_category,
                     "paidOn": r.declared_date, "confirmedAt": r.confirmed_at,
                     "evidence": EVIDENCE_LABEL.get(r.provenance, r.provenance)}
            if role != "LANDLORD" or landlord_is_payee:
                entry["externalReference"] = r.external_reference
            items.append(entry)
    obligation_ids = [o.id for o in obligations]
    if obligation_ids:
        for ret in db.scalars(select(RentalPaymentReturn).where(RentalPaymentReturn.obligation_id.in_(obligation_ids))):
            items.append({"kind": "RETURN", "at": ret.created_at, "obligationId": ret.obligation_id, "returnId": ret.id,
                          "purpose": ret.kind, "amount": float(ret.amount), "currency": ret.currency,
                          "status": ret.status, "method": ret.payment_method_category,
                          "evidence": "Return recorded by payee" if ret.status == "RECORDED" else
                          "Return confirmed by payer" if ret.status == "CONFIRMED" else "Return disputed"})
    items.sort(key=lambda i: (i["at"] or _now()))
    totals = {"due": round(sum(o.outstanding_amount for o in obligations if o.status not in ("CANCELLED", "WAIVED")), 2),
              "paid": round(sum(float(r.confirmed_amount or 0) for o in obligations for r in o.records
                                if r.status in ("CONFIRMED", "PARTIALLY_PAID")), 2)}
    return {"subletRequestId": sublet.id, "role": role, "items": items, "totals": totals,
            "currency": obligations[0].currency if obligations else ""}


# -- how to pay (payer) -------------------------------------------------------------------------

def how_to_pay(db: Session, obligation: RentalPaymentObligation) -> dict:
    """Screens C/D/E for the payer: payee, why, amount, the payee's direct
    payment details (bank account or UPI ID) and the reference to quote.
    Full details only after the agreement is accepted; every view is audited
    by the caller."""
    from app.core.field_encryption import decrypt_json
    from app.crud.rental_payment import get_active_rental_payment_instruction

    sublet = sublet_for_obligation(db, obligation)
    a = current_arrangement(db, sublet.id) if sublet else None
    blocked = ""
    try:
        payment_gate(db, obligation)
    except HTTPException as exc:
        blocked = exc.detail
    agreement = obligation.agreement
    accepted = agreement is None or bool(getattr(agreement, "signed_by_renter_at", None)) or \
        agreement.status in ("PAYMENT_IN_PROGRESS", "SIGNED", "ACTIVE")
    instruction = get_active_rental_payment_instruction(db, obligation.recipient_party_id)
    methods = list(a.accepted_methods) if a else ["BANK_TRANSFER", "UPI", "CASH"]
    details = None
    if instruction is not None and accepted and not blocked and instruction.method in methods:
        details = decrypt_json(instruction.encrypted_bank_details) if instruction.encrypted_bank_details else {}
    custodian = None
    if a and obligation.obligation_type == "DEPOSIT" and a.deposit_route == "PAY_TO_CUSTODIAN":
        custodian = {"name": a.custodian_name, "reference": a.custodian_reference, "instructions": a.custodian_instructions}
        details = None
    basis = obligation.payee_basis or (a.rent_payee_basis if a else "")
    return {
        "obligationId": obligation.id, "purpose": obligation.obligation_type, "label": obligation.display_label,
        "amount": float(obligation.amount), "outstanding": obligation.outstanding_amount, "currency": obligation.currency,
        "dueDate": obligation.due_date, "periodStart": obligation.period_start, "periodEnd": obligation.period_end,
        "platformFee": 0.0, "total": obligation.outstanding_amount,
        "payee": {"name": instruction.recipient_name if instruction else _party_name(db, obligation.recipient_party_id),
                  "type": obligation.payee_type, "why": PAYEE_BASIS_TEXT.get(basis, "")},
        "acceptedMethods": methods, "paymentReference": obligation.payment_reference or f"ZR-{obligation.id}",
        "method": instruction.method if instruction else "", "countryCode": instruction.country_code if instruction else "",
        "details": details, "custodian": custodian,
        "additionalInstructions": instruction.additional_instructions if instruction and details is not None else "",
        "blockedReason": blocked or ("" if accepted else "Payment details appear once the agreement is accepted."),
        "setupIncomplete": instruction is None and "CASH" not in methods,
        "zoikoNotice": "Zoiko Rooms does not receive this payment. You pay the payee directly.",
    }


# -- notifications ------------------------------------------------------------------------------

def _users_for(db: Session, party_ids: list[int | None]) -> list[UserAccount]:
    ids = [p for p in dict.fromkeys(party_ids) if p]
    return list(db.scalars(select(UserAccount).where(UserAccount.party_id.in_(ids)))) if ids else []


def notify_parties(db: Session, sublet: SubletRequest, title: str, message: str, *, exclude_user_id: int | None = None) -> None:
    from app.crud import notification as notif_crud

    parties = [sublessor_party_id(db, sublet), sublet.proposed_renter_party_id, landlord_payee(db, sublet)[0]]
    for user in _users_for(db, parties):
        if user.id == exclude_user_id:
            continue
        notif_crud.notify_user_by_party(db, user.party_id, title=title, message=message,
                                        notification_type="sublet.payment", related_entity_type="sublet_request",
                                        related_entity_id=str(sublet.id))
    db.commit()


def _notify_setup_incomplete(db: Session, a: SubletPaymentArrangement) -> None:
    from app.crud import notification as notif_crud

    result = evaluate(db, a)
    if "PAYMENT_SETUP_INCOMPLETE" in result["rent"]["reason_codes"] and a.rent_payee_party_id:
        notif_crud.notify_user_by_party(
            db, a.rent_payee_party_id, title="Add your payment details",
            message="A sublet tenant needs to pay you, but you haven't added confirmed bank or UPI details. "
                    "Add them under Payments so they can pay.",
            notification_type="sublet.payee_setup_incomplete", related_entity_type="sublet_request",
            related_entity_id=str(a.sublet_request_id))
        db.commit()


def on_payment_confirmed(db: Session, obligation: RentalPaymentObligation) -> None:
    """Section 16: the landlord sees a confirmed sublet payment when they
    aren't the payee; locks the arrangement."""
    from app.crud import notification as notif_crud

    sublet = sublet_for_obligation(db, obligation)
    if sublet is None:
        return
    a = current_arrangement(db, sublet.id)
    if a is not None:
        is_locked(db, a)
    landlord_id = landlord_payee(db, sublet)[0]
    if landlord_id and landlord_id != obligation.recipient_party_id:
        notif_crud.notify_user_by_party(
            db, landlord_id, title="Sublet payment confirmed",
            message=f"A {obligation.display_label} payment of {float(obligation.amount):.2f} {obligation.currency} "
                    f"for an approved sublet at your property was confirmed by its payee.",
            notification_type="sublet.payment_confirmed", related_entity_type="sublet_request",
            related_entity_id=str(sublet.id))
    db.commit()


def on_sublet_approved(db: Session, sublet: SubletRequest) -> None:
    """Create the arrangement from the obligations just created at approval."""
    if sublet.new_agreement_id is None or sublet.arrangement_type not in ARRANGEMENT_TYPES_WITH_OWN_PAYMENTS:
        return
    obligations = obligations_for_sublet(db, sublet)
    rent = next((o for o in obligations if o.obligation_type == "RENT"), None)
    deposit = next((o for o in obligations if o.obligation_type == "DEPOSIT"), None)
    if rent is None:
        return
    a = ensure_arrangement(db, sublet, rent_amount=float(rent.amount), deposit_amount=float(deposit.amount) if deposit else 0.0,
                           currency=rent.currency, first_payment_date=rent.due_date)
    if a is None:
        return
    apply_to_open_obligations(db, sublet, a)
    db.commit()
    _notify_setup_incomplete(db, a)
