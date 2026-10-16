"""Account settings (crud/user_settings.py): preferences, profile photo, sign-in
email change, signing out other sessions, data export and account closure."""

import json

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from pydantic import Field
from sqlalchemy.orm import Session

from app.api.deps import USER_COOKIE_NAME, get_current_user, get_db
from app.api.routes.user_auth import _set_user_cookie
from app.core.config import settings
from app.core.mailer import send_email_change_confirmation_email
from app.crud import user_settings as crud
from app.crud.audit import log_audit_event
from app.models.user_account import UserAccount
from app.schemas.common import CamelModel
from app.schemas.user import UserRead

router = APIRouter(prefix="/api/users", tags=["user-account-settings"])


class _EmailChangeRequest(CamelModel):
    new_email: str = Field(min_length=3, max_length=255)
    current_password: str


class _EmailChangeConfirm(CamelModel):
    token: str = Field(min_length=10, max_length=200)


class _CloseAccountRequest(CamelModel):
    password: str
    reason: str = Field(default="", max_length=500)


# Preferences are a flat map validated against crud.PREFERENCE_FIELDS, so the
# request body is a plain dict rather than a fixed model.
@router.get("/preferences")
def get_preferences(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return crud.get_preferences(db, user)


@router.put("/preferences")
def update_preferences(changes: dict, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return crud.update_preferences(db, user, changes)


@router.post("/profile/avatar", response_model=UserRead)
async def upload_avatar(
    file: UploadFile = File(...), user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    return await crud.save_avatar(db, user, file)


@router.delete("/profile/avatar", response_model=UserRead)
def delete_avatar(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return crud.remove_avatar(db, user)


@router.get("/profile/avatar")
def get_avatar(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return crud.avatar_response(db, user)


@router.get("/email-change")
def get_pending_email_change(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return {"pendingEmail": crud.pending_email_change(db, user)}


@router.post("/email-change", status_code=status.HTTP_202_ACCEPTED)
def request_email_change(
    payload: _EmailChangeRequest, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict:
    """Emails a confirmation link to the new address; the change applies only
    once it's opened."""
    raw = crud.request_email_change(db, user, payload.new_email, payload.current_password)
    link = f"{settings.frontend_url}/account/confirm-email?token={raw}"
    send_email_change_confirmation_email(payload.new_email.strip().lower(), link, settings.email_change_token_expire_minutes, user.full_name)
    log_audit_event(db, None, "user.email_change_requested", "user_account", str(user.id), reason=f"user:{user.id}")
    db.commit()
    return {"pendingEmail": payload.new_email.strip().lower()}


@router.post("/email-change/confirm", response_model=UserRead)
def confirm_email_change(payload: _EmailChangeConfirm, response: Response, db: Session = Depends(get_db)):
    """Opened from the link in the new inbox -- no session needed. Sessions are
    keyed by email, so the browser that confirms gets a fresh cookie."""
    user = crud.confirm_email_change(db, payload.token)
    log_audit_event(db, None, "user.email_changed", "user_account", str(user.id), reason=f"user:{user.id}")
    db.commit()
    _set_user_cookie(response, user.email)
    return user


@router.post("/sessions/revoke-others")
def revoke_other_sessions(response: Response, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    crud.revoke_sessions(db, user)
    _set_user_cookie(response, user.email)
    return {"ok": True}


@router.get("/data-export")
def download_data_export(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    body = json.dumps(crud.build_data_export(db, user), indent=2, default=str)
    log_audit_event(db, None, "user.data_export", "user_account", str(user.id), reason=f"user:{user.id}")
    db.commit()
    return Response(
        content=body, media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="zoiko-rooms-data-export.json"', "Cache-Control": "no-store"},
    )


@router.get("/account/closure-check")
def closure_check(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    blockers = crud.closure_blockers(db, user)
    return {"canClose": not blockers, "blockers": blockers}


@router.post("/account/close")
def close_account(
    payload: _CloseAccountRequest, response: Response,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict:
    crud.close_account(db, user, payload.password, payload.reason)
    log_audit_event(db, None, "user.account_closed", "user_account", str(user.id), reason=f"user:{user.id}")
    db.commit()
    response.delete_cookie(USER_COOKIE_NAME, path="/", domain=settings.cookie_domain)
    return {"ok": True}
