from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_super_admin
from app.core.correlation import get_correlation_id
from app.crud.audit import log_audit_event
from app.crud.authority import (
    get_authority_record,
    list_authority_records,
    reject_authority_record,
    submit_authority_record,
    verify_authority_record,
)
from app.crud.events import emit_event
from app.crud.party import assert_provider_access, party_id_for_room
from app.crud.property import get_room
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.schemas.marketplace import AuthorityRecordCreate, AuthorityRecordRead

router = APIRouter(prefix="/api/authority-records", tags=["authority"], dependencies=[Depends(get_current_admin)])


@router.get("", response_model=list[AuthorityRecordRead])
def get_records(room_id: int | None = None, db: Session = Depends(get_db)):
    return list_authority_records(db, room_id)


@router.post("", response_model=AuthorityRecordRead, status_code=status.HTTP_201_CREATED)
def post_record(
    payload: AuthorityRecordCreate,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    room = get_room(db, payload.room_id)
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    assert_provider_access(db, admin, party_id_for_room(room))
    return submit_authority_record(db, admin, room, payload)


@router.post("/{authority_id}/verify", response_model=AuthorityRecordRead, dependencies=[Depends(require_super_admin)])
def verify_record(
    authority_id: int,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    record = get_authority_record(db, authority_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Authority record not found")
    updated = verify_authority_record(db, record, admin)
    log_audit_event(db, admin, "authority.verify", "authority_record", str(authority_id), get_correlation_id(request))
    emit_event(db, "authority.verified", "authority_record", str(authority_id), {"room_id": record.room_id})
    db.commit()
    return updated


@router.post("/{authority_id}/reject", response_model=AuthorityRecordRead, dependencies=[Depends(require_super_admin)])
def reject_record(
    authority_id: int,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    record = get_authority_record(db, authority_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Authority record not found")
    updated = reject_authority_record(db, record, admin)
    log_audit_event(db, admin, "authority.reject", "authority_record", str(authority_id), get_correlation_id(request))
    db.commit()
    return updated
