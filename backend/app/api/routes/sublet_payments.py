"""ZR-SUBLET-PAY-003 Section 13 API surface (direct bank / UPI / cash).

- GET/POST /api/users/sublets/{id}/payment-arrangement -- server-authoritative
  payee, deposit route, accepted methods and terms; the sublessor or landlord
  may change it (If-Match version; an amendment once locked).
- GET /api/users/sublets/{id}/transactions -- one role-minimized timeline.
- GET /api/users/rental-payments/obligations/{id}/how-to-pay -- the payer's
  payee, amount, reference and direct payment details (audited).
- GET /api/users/rental-payments/records/{id}/receipt -- a receipt for a
  payment the payee confirmed.
"""

from __future__ import annotations

import io

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.correlation import get_correlation_id
from app.crud import rental_payment as rp_crud
from app.crud.audit import log_audit_event
from app.crud.guest import get_guest_for_user
from app.db.session import get_db
from app.models.user_account import UserAccount
from app.schemas.common import CamelModel
from app.services import sublet_payments as svc

router = APIRouter(prefix="/api/users", tags=["sublet-payments"], dependencies=[Depends(get_current_user)])


class ArrangementIn(CamelModel):
    rent_payee_type: str | None = None
    accepted_methods: list[str] | None = None
    deposit_route: str | None = None
    custodian_name: str | None = None
    custodian_reference: str | None = None
    custodian_instructions: str | None = None
    amendment_reason: str = ""


def _version(if_match: str | None) -> int | None:
    if not if_match:
        return None
    try:
        return int(if_match.strip().strip('"'))
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "If-Match must be the arrangement version")


@router.get("/sublets/{sublet_id}/payment-arrangement")
def get_arrangement(sublet_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    sublet, role = svc.get_sublet_for_viewer(db, user, sublet_id)
    return svc.arrangement_view(db, sublet, role)


@router.post("/sublets/{sublet_id}/payment-arrangement")
def post_arrangement(sublet_id: int, payload: ArrangementIn, if_match: str | None = Header(default=None, alias="If-Match"),
                     user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    sublet, _role = svc.get_sublet_for_viewer(db, user, sublet_id)
    changes = payload.model_dump(exclude_unset=True, exclude={"amendment_reason"})
    svc.update_arrangement(db, user, sublet, changes, expected_version=_version(if_match),
                           amendment_reason=payload.amendment_reason)
    sublet, role = svc.get_sublet_for_viewer(db, user, sublet_id)
    return svc.arrangement_view(db, sublet, role)


@router.get("/sublets/{sublet_id}/transactions")
def get_transactions(sublet_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    sublet, role = svc.get_sublet_for_viewer(db, user, sublet_id)
    return svc.transactions_view(db, sublet, role)


@router.get("/rental-payments/obligations/{obligation_id}/how-to-pay")
def get_how_to_pay(obligation_id: int, request: Request, user: UserAccount = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    guest = get_guest_for_user(db, user)
    if guest is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No renter record for this account")
    obligation = rp_crud.get_obligation_or_404(db, obligation_id)
    rp_crud.assert_tenant_owns_obligation(obligation, guest.id)
    view = svc.how_to_pay(db, obligation)
    if view["details"] is not None:
        # Full account / UPI details are sensitive: every view is audited.
        log_audit_event(db, None, "rental_payment.instructions_viewed", "rental_payment_obligation", str(obligation.id),
                        get_correlation_id(request), reason=f"user:{user.id}")
        db.commit()
    return view


def _receipt_pdf(lines: list[tuple[str, str]], title: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    page.setFont("Helvetica-Bold", 16)
    page.drawString(56, height - 72, title)
    page.setFont("Helvetica", 10)
    y = height - 110
    for label, value in lines:
        page.setFont("Helvetica-Bold", 10)
        page.drawString(56, y, label)
        page.setFont("Helvetica", 10)
        page.drawString(220, y, str(value)[:90])
        y -= 18
    page.setFont("Helvetica-Oblique", 9)
    page.drawString(56, y - 16, "Paid directly to the payee. Zoiko Rooms did not receive, hold or forward this payment.")
    page.showPage()
    page.save()
    return buffer.getvalue()


@router.get("/rental-payments/records/{record_id}/receipt")
def get_receipt(record_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """Screen F "Download receipt" -- only for a payment its payee confirmed
    (a self-reported payment isn't proof of money movement)."""
    record = rp_crud.get_record_or_404(db, record_id)
    guest = get_guest_for_user(db, user)
    is_payer = guest is not None and record.declared_by_guest_id == guest.id
    is_payee = bool(user.party_id) and record.obligation.recipient_party_id == user.party_id
    if not (is_payer or is_payee):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't download this receipt")
    if record.status not in ("CONFIRMED", "PARTIALLY_PAID"):
        raise HTTPException(status.HTTP_409_CONFLICT, "A receipt is available once the payee confirms the payment")
    o = record.obligation
    reference = f"PAY-{o.payment_reference or o.id}-{record.id}"
    lines = [
        ("Receipt reference", reference),
        ("Payment for", f"{o.display_label.capitalize()} -- due {o.due_date:%d %b %Y}"),
        ("Period", f"{o.period_start:%d %b %Y} - {o.period_end:%d %b %Y}" if o.period_start and o.period_end else "--"),
        ("Amount", f"{float(record.confirmed_amount or record.declared_amount):.2f} {record.declared_currency}"),
        ("Paid on", f"{record.declared_date:%d %b %Y}"),
        ("Method", record.payment_method_category.replace("_", " ").title()),
        ("Payer reference / UTR", record.external_reference or "--"),
        ("Payee", svc._party_name(db, o.recipient_party_id)),
        ("Confirmed by payee", f"{record.confirmed_at:%d %b %Y %H:%M}" if record.confirmed_at else "--"),
        ("Zoiko Rooms platform fee", f"0.00 {o.currency}"),
    ]
    pdf = _receipt_pdf(lines, "Payment receipt")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{reference}.pdf"', "Cache-Control": "no-store"})
