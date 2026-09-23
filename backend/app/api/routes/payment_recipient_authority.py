from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_super_admin
from app.core.correlation import get_correlation_id
from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.crud.payment_recipient_authority import (
    get_payment_recipient_authority_or_404,
    list_payment_recipient_authorities,
    reject_payment_recipient_authority,
    revoke_payment_recipient_authority,
    verify_payment_recipient_authority,
)
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.schemas.payment_recipient_authority import PaymentRecipientAuthorityRead, PaymentRecipientAuthorityRevoke

router = APIRouter(
    prefix="/api/payment-recipient-authorities", tags=["payment-recipient-authority"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=list[PaymentRecipientAuthorityRead])
def get_payment_recipient_authorities(room_id: int | None = None, db: Session = Depends(get_db)):
    return list_payment_recipient_authorities(db, room_id)


@router.post(
    "/{authority_id}/verify", response_model=PaymentRecipientAuthorityRead, dependencies=[Depends(require_super_admin)],
)
def verify_payment_recipient_authority_route(
    authority_id: int,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    record = get_payment_recipient_authority_or_404(db, authority_id)
    updated = verify_payment_recipient_authority(db, record, admin)
    log_audit_event(
        db, admin, "payment_recipient_authority.verify", "payment_recipient_authority", str(authority_id),
        get_correlation_id(request),
    )
    emit_event(db, "payment_recipient.authority_verified", "payment_recipient_authority", str(authority_id), {"room_id": record.room_id})
    db.commit()
    return updated


@router.post(
    "/{authority_id}/reject", response_model=PaymentRecipientAuthorityRead, dependencies=[Depends(require_super_admin)],
)
def reject_payment_recipient_authority_route(
    authority_id: int,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    record = get_payment_recipient_authority_or_404(db, authority_id)
    updated = reject_payment_recipient_authority(db, record, admin)
    log_audit_event(
        db, admin, "payment_recipient_authority.reject", "payment_recipient_authority", str(authority_id),
        get_correlation_id(request),
    )
    db.commit()
    return updated


@router.post(
    "/{authority_id}/revoke", response_model=PaymentRecipientAuthorityRead, dependencies=[Depends(require_super_admin)],
)
def revoke_payment_recipient_authority_route(
    authority_id: int,
    payload: PaymentRecipientAuthorityRevoke,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    record = get_payment_recipient_authority_or_404(db, authority_id)
    updated = revoke_payment_recipient_authority(db, record, admin)
    log_audit_event(
        db, admin, "payment_recipient_authority.revoke", "payment_recipient_authority", str(authority_id),
        get_correlation_id(request), reason=payload.reason,
    )
    emit_event(db, "payment_connection.suspended", "payment_recipient_authority", str(authority_id), {"room_id": record.room_id})
    db.commit()
    return updated
