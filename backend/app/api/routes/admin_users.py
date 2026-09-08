from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_super_admin
from app.crud.admin import get_admin_by_email
from app.crud.admin_user import (
    create_admin_user,
    delete_admin_user,
    get_admin_user,
    list_admin_users,
    set_admin_approval_status,
    update_admin_user,
)
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.schemas.admin_user import AdminUserCreate, AdminUserRead, AdminUserUpdate

router = APIRouter(prefix="/api/admin-users", tags=["admin-users"], dependencies=[Depends(require_super_admin)])


def _get_or_404(db: Session, admin_id: int) -> AdminUser:
    admin = get_admin_user(db, admin_id)
    if not admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Admin not found")
    return admin


@router.get("", response_model=list[AdminUserRead])
def get_admin_users(db: Session = Depends(get_db)):
    return list_admin_users(db)


@router.post("", response_model=AdminUserRead, status_code=status.HTTP_201_CREATED)
def post_admin_user(
    payload: AdminUserCreate,
    acting_admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    if get_admin_by_email(db, payload.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "An admin with this email already exists")
    if payload.role not in ("admin", "super_admin"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Role must be 'admin' or 'super_admin'")
    return create_admin_user(db, payload, acting_admin)


@router.put("/{admin_id}", response_model=AdminUserRead)
def put_admin_user(
    admin_id: int,
    payload: AdminUserUpdate,
    acting_admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    if payload.role is not None and payload.role not in ("admin", "super_admin"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Role must be 'admin' or 'super_admin'")
    target = _get_or_404(db, admin_id)
    return update_admin_user(db, target, payload, acting_admin)


@router.delete("/{admin_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_admin_user_route(
    admin_id: int,
    reassign_to: int | None = None,
    force: bool = False,
    acting_admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    target = _get_or_404(db, admin_id)
    delete_admin_user(db, target, acting_admin, reassign_to_id=reassign_to, force=force)


@router.post("/{admin_id}/approve", response_model=AdminUserRead)
def approve_admin_user(admin_id: int, db: Session = Depends(get_db)):
    target = _get_or_404(db, admin_id)
    return set_admin_approval_status(db, target, "approved")


@router.post("/{admin_id}/reject", response_model=AdminUserRead)
def reject_admin_user(admin_id: int, db: Session = Depends(get_db)):
    target = _get_or_404(db, admin_id)
    return set_admin_approval_status(db, target, "rejected")
